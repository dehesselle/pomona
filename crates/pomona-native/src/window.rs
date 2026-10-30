// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! The window: winit's event loop on the calling thread, showing a picture.
//!
//! The session's reader thread draws on the canvas and wakes the loop through
//! the picture's [`Waker`]; the loop then uploads what changed and redraws.
//! Wake-ups for the picture are coalesced: one is outstanding at a time, and
//! the upload takes everything that changed since.
//!
//! The loop holds no GIL. It takes it only to tell Python about the window
//! (`on_event`), and stops at the first exception that raises.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;
use winit::application::ApplicationHandler;
use winit::dpi::{PhysicalPosition, PhysicalSize};
use winit::event::WindowEvent;
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop, EventLoopProxy};
use winit::window::{CustomCursor, Window, WindowId};

use crate::gpu::{Frame, Gpu};
use crate::picture::Shared;

/// Why the loop is woken.
#[derive(Debug, Clone, Copy)]
pub enum Wake {
    /// The canvas changed.
    Picture,
    /// The pointer has a new shape.
    Cursor,
    /// The session is over.
    Quit,
}

/// How the picture reaches a window, if one is open.
#[derive(Default)]
pub struct Waker {
    proxy: Mutex<Option<EventLoopProxy<Wake>>>,
    picture_pending: AtomicBool,
    quit: AtomicBool,
}

impl Waker {
    pub fn picture(&self) {
        if !self.picture_pending.swap(true, Ordering::AcqRel) {
            self.send(Wake::Picture);
        }
    }

    pub fn cursor(&self) {
        self.send(Wake::Cursor);
    }

    pub fn quit(&self) {
        self.quit.store(true, Ordering::Release);
        self.send(Wake::Quit);
    }

    fn send(&self, wake: Wake) {
        if let Some(proxy) = &*self.proxy.lock().unwrap_or_else(|p| p.into_inner()) {
            // A loop that already ended has nothing left to wake.
            let _ = proxy.send_event(wake);
        }
    }

    fn attach(&self, proxy: Option<EventLoopProxy<Wake>>) {
        *self.proxy.lock().unwrap_or_else(|p| p.into_inner()) = proxy;
    }
}

/// The pointer's shape, as the window takes it.
pub struct CursorImage {
    pub width: u16,
    pub height: u16,
    pub hotspot: (u16, u16),
    pub rgba: Vec<u8>,
}

/// Open a window on `shared` and run until it is closed or the session quits.
pub fn run(shared: Arc<Shared>, on_event: Py<PyAny>, title: String) -> PyResult<()> {
    let mut builder = EventLoop::<Wake>::with_user_event();
    #[cfg(target_os = "windows")]
    {
        // A library cannot promise to be called on the main thread; Windows
        // does not need it. macOS does, and enforces it.
        use winit::platform::windows::EventLoopBuilderExtWindows;
        builder.with_any_thread(true);
    }
    let event_loop = builder
        .build()
        .map_err(|error| window_error(format!("no event loop: {error}")))?;
    event_loop.set_control_flow(ControlFlow::Wait);
    shared.waker.attach(Some(event_loop.create_proxy()));
    let mut app = App {
        shared: shared.clone(),
        on_event,
        title,
        gpu: None,
        cursor: None,
        error: None,
    };
    let ran = event_loop.run_app(&mut app);
    shared.waker.attach(None);
    if let Some(error) = app.error {
        return Err(error);
    }
    ran.map_err(|error| window_error(format!("the event loop failed: {error}")))
}

fn window_error(message: String) -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err(message)
}

struct App {
    shared: Arc<Shared>,
    on_event: Py<PyAny>,
    title: String,
    gpu: Option<Gpu>,
    /// Kept so the shape stays alive while the window shows it.
    cursor: Option<CustomCursor>,
    error: Option<PyErr>,
}

impl App {
    fn fail(&mut self, event_loop: &ActiveEventLoop, error: PyErr) {
        self.error.get_or_insert(error);
        event_loop.exit();
    }

    /// Tell Python something about the window.
    fn tell(&mut self, event_loop: &ActiveEventLoop, call: impl FnOnce(Python<'_>, &Py<PyAny>) -> PyResult<Py<PyAny>>) {
        let result = Python::attach(|py| call(py, &self.on_event));
        if let Err(error) = result {
            self.fail(event_loop, error);
        }
    }

    fn open(&mut self, event_loop: &ActiveEventLoop) -> Result<(), String> {
        let (width, height) = {
            let canvas = self.shared.canvas();
            (canvas.width() as u32, canvas.height() as u32)
        };
        // The picture at one to one, unless the screen is too small for it.
        let mut size = PhysicalSize::new(width.max(320), height.max(200));
        let mut attributes = Window::default_attributes().with_title(self.title.clone());
        if let Some(monitor) = event_loop
            .primary_monitor()
            .or_else(|| event_loop.available_monitors().next())
        {
            let room = monitor.size();
            let fit = (0.9 * room.width as f64 / size.width as f64).min(0.9 * room.height as f64 / size.height as f64);
            if fit < 1.0 {
                size = PhysicalSize::new((size.width as f64 * fit) as u32, (size.height as f64 * fit) as u32);
            }
            // Centred, a little high for the title bar.
            let origin = monitor.position();
            attributes = attributes.with_position(PhysicalPosition::new(
                origin.x + (room.width.saturating_sub(size.width) / 2) as i32,
                origin.y + (room.height.saturating_sub(size.height) / 2) as i32 / 2,
            ));
        }
        let attributes = attributes.with_inner_size(size);
        let window = Arc::new(
            event_loop
                .create_window(attributes)
                .map_err(|error| format!("no window: {error}"))?,
        );
        let mut gpu = Gpu::new(window.clone())?;
        {
            let mut canvas = self.shared.canvas();
            self.shared.waker.picture_pending.store(false, Ordering::Release);
            let mut damage = canvas.take_damage();
            damage.resized = true;
            gpu.upload(&canvas, &damage);
        }
        let scale = window.scale_factor();
        let adapter = gpu.adapter.clone();
        self.gpu = Some(gpu);
        self.tell(event_loop, |py, f| f.call1(py, ("opened", adapter, scale)));
        self.set_cursor(event_loop);
        window.request_redraw();
        Ok(())
    }

    fn upload(&mut self) {
        let Some(gpu) = &mut self.gpu else {
            return;
        };
        let mut canvas = self.shared.canvas();
        // Cleared before taking the damage: a change that lands after this
        // wakes the loop again.
        self.shared.waker.picture_pending.store(false, Ordering::Release);
        let damage = canvas.take_damage();
        if damage.resized || !damage.rects.is_empty() {
            gpu.upload(&canvas, &damage);
            drop(canvas);
            gpu.window.request_redraw();
        }
    }

    fn set_cursor(&mut self, event_loop: &ActiveEventLoop) {
        let Some(gpu) = &self.gpu else {
            return;
        };
        let source = {
            let shape = self.shared.cursor.lock().unwrap_or_else(|p| p.into_inner());
            let Some(shape) = &*shape else {
                return;
            };
            CustomCursor::from_rgba(
                shape.rgba.clone(),
                shape.width,
                shape.height,
                shape.hotspot.0.min(shape.width.saturating_sub(1)),
                shape.hotspot.1.min(shape.height.saturating_sub(1)),
            )
        };
        // A shape the platform cannot take leaves the one shown.
        if let Ok(source) = source {
            let cursor = event_loop.create_custom_cursor(source);
            gpu.window.set_cursor(cursor.clone());
            self.cursor = Some(cursor);
        }
    }
}

impl ApplicationHandler<Wake> for App {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.shared.waker.quit.load(Ordering::Acquire) {
            event_loop.exit();
            return;
        }
        if self.gpu.is_none()
            && let Err(message) = self.open(event_loop)
        {
            self.fail(event_loop, window_error(message));
        }
    }

    fn user_event(&mut self, event_loop: &ActiveEventLoop, wake: Wake) {
        match wake {
            Wake::Picture => self.upload(),
            Wake::Cursor => self.set_cursor(event_loop),
            Wake::Quit => event_loop.exit(),
        }
    }

    fn window_event(&mut self, event_loop: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
        match event {
            WindowEvent::CloseRequested => event_loop.exit(),
            WindowEvent::Resized(size) => {
                if let Some(gpu) = &mut self.gpu {
                    gpu.resize(size.width, size.height);
                    gpu.window.request_redraw();
                }
            }
            WindowEvent::ScaleFactorChanged { scale_factor, .. } => {
                self.tell(event_loop, |py, f| f.call1(py, ("scale", scale_factor)))
            }
            WindowEvent::RedrawRequested => {
                let Some(gpu) = &mut self.gpu else {
                    return;
                };
                match gpu.render() {
                    Ok(Frame::Presented) => {
                        self.shared.frames.fetch_add(1, Ordering::Relaxed);
                    }
                    Ok(Frame::Skipped) => {}
                    Ok(Frame::Again) => gpu.window.request_redraw(),
                    Err(message) => self.fail(event_loop, window_error(message)),
                }
            }
            _ => {}
        }
    }
}
