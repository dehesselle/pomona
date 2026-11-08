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
//! Input goes to Python as it happens, as `on_event("key", keysym, down)`,
//! `on_event("pointer", x, y, buttons)` and `on_event("scroll", dx, dy, x,
//! y)`, positions in the picture's pixels, buttons as RFB numbers them, scroll
//! distances in points. Pointer motion is coalesced: what moved during one
//! pass of the loop goes out once, before the next key or click.
//!
//! The loop holds no GIL. It takes it only to tell Python about the window
//! (`on_event`), and stops at the first exception that raises.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use pyo3::prelude::*;
use winit::application::ApplicationHandler;
use winit::dpi::{PhysicalPosition, PhysicalSize};
use winit::event::{ElementState, KeyEvent, MouseButton, MouseScrollDelta, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, DeviceEvents, EventLoop, EventLoopProxy};
#[cfg(target_os = "windows")]
use winit::keyboard::Key;
use winit::keyboard::PhysicalKey;
use winit::platform::modifier_supplement::KeyEventExtModifierSupplement;
use winit::window::{CustomCursor, Window, WindowButtons, WindowId};

use crate::clipboard::Pasteboard;
use crate::gpu::{Frame, Gpu};
use crate::keys::{KeyInput, Keyboard, Press};
use crate::picture::Shared;

/// The narrowest the window gets, in pixels.
const MIN_WIDTH: u32 = 320;
/// How much of the screen the window may take.
const ROOM: f64 = 0.9;

/// How far one notch of a mouse wheel scrolls, in points.
const POINTS_PER_NOTCH: f64 = 40.0;

/// Why the loop is woken.
#[derive(Debug, Clone, Copy)]
pub enum Wake {
    /// The canvas changed.
    Picture,
    /// The pointer has a new shape.
    Cursor,
    /// The session is over.
    Quit,
    /// The window is to take a new shape or size.
    Fit,
    /// The Mac's pasteboard has new text.
    Pasteboard,
    /// Keys were grabbed from the system.
    #[cfg_attr(not(target_os = "windows"), allow(dead_code))]
    Keys,
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

    pub fn fit(&self) {
        self.send(Wake::Fit);
    }

    pub fn pasteboard(&self) {
        self.send(Wake::Pasteboard);
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

/// The window's shape and size, as Python asks for them.
pub struct Fit {
    /// The most the Mac can send, which also gives the aspect.
    pub max: (u32, u32),
    /// The size wanted, if the screen has room.
    pub want: (u32, u32),
}

/// How a window behaves.
pub struct Options {
    pub title: String,
    /// Send Control as Command and the Windows key as Control.
    pub ctrl_as_command: bool,
    /// Share the local pasteboard with the Mac.
    pub pasteboard: bool,
}

/// Open a window on `shared` and run until it is closed or the session quits.
pub fn run(shared: Arc<Shared>, on_event: Py<PyAny>, options: Options) -> PyResult<()> {
    let mut builder = EventLoop::<Wake>::with_user_event();
    #[cfg(target_os = "windows")]
    {
        // A library cannot promise to be called on the main thread; Windows
        // and Linux do not need it. macOS does, and enforces it.
        use winit::platform::windows::EventLoopBuilderExtWindows;
        builder.with_any_thread(true);
    }
    #[cfg(target_os = "linux")]
    {
        // X11 and Wayland share the setting.
        use winit::platform::x11::EventLoopBuilderExtX11;
        builder.with_any_thread(true);
    }
    #[cfg(target_os = "macos")]
    {
        // winit's menu would keep Command-Q and Command-H from the Mac.
        use winit::platform::macos::EventLoopBuilderExtMacOS;
        builder.with_default_menu(false);
    }
    let event_loop = builder
        .build()
        .map_err(|error| window_error(format!("no event loop: {error}")))?;
    event_loop.set_control_flow(ControlFlow::Wait);
    // No device events: Pomona uses none, and on Windows the raw keyboard
    // input winit registers for them keeps every low-level keyboard hook,
    // the grab's included, from hearing keys while the window is in front.
    event_loop.listen_device_events(DeviceEvents::Never);
    shared.waker.attach(Some(event_loop.create_proxy()));
    let mut app = App {
        shared: shared.clone(),
        on_event,
        title: options.title,
        #[cfg(target_os = "windows")]
        proxy: event_loop.create_proxy(),
        gpu: None,
        cursor: None,
        error: None,
        keyboard: Keyboard::new(options.ctrl_as_command),
        pasteboard: options.pasteboard.then(Pasteboard::default),
        pointer: Pointer::default(),
        aspect: 0.0,
        placed: false,
        #[cfg(target_os = "windows")]
        grab: None,
    };
    let ran = event_loop.run_app(&mut app);
    shared.waker.attach(None);
    #[cfg(target_os = "windows")]
    drop(app.grab.take());
    if let Some(error) = app.error {
        return Err(error);
    }
    ran.map_err(|error| window_error(format!("the event loop failed: {error}")))
}

/// Give the window its icons, drawn for `scale`: the small one for the title
/// bar and, on Windows, the large one for the taskbar and Alt-Tab, which would
/// otherwise scale the small one up. macOS has no window icons, and winit sets
/// none on Wayland.
fn set_icons(window: &Window, scale: f64) {
    let size = |points: f64| (points * scale).round() as u32;
    window.set_window_icon(crate::icon::icon(size(16.0)));
    #[cfg(target_os = "windows")]
    {
        use winit::platform::windows::WindowExtWindows;
        window.set_taskbar_icon(crate::icon::icon(size(32.0)));
    }
}

fn window_error(message: String) -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err(message)
}

/// The pointer, as the Mac last heard of it and as it is now.
#[derive(Default)]
struct Pointer {
    /// Where it is on the picture, once it has been over the window.
    position: Option<(u16, u16)>,
    /// RFB's button bits: 1 left, 2 middle, 4 right.
    buttons: u8,
    /// Moved since the Mac last heard.
    moved: bool,
    /// Scrolling not yet sent, less than a point.
    scroll: (f64, f64),
}

struct App {
    shared: Arc<Shared>,
    on_event: Py<PyAny>,
    title: String,
    #[cfg(target_os = "windows")]
    proxy: EventLoopProxy<Wake>,
    gpu: Option<Gpu>,
    /// Kept so the shape stays alive while the window shows it.
    cursor: Option<CustomCursor>,
    error: Option<PyErr>,
    keyboard: Keyboard,
    /// None when the pasteboard is not shared.
    pasteboard: Option<Pasteboard>,
    pointer: Pointer,
    /// The picture's width over its height, which the window keeps.
    aspect: f64,
    /// Whether the window was placed for its first fitted size.
    placed: bool,
    #[cfg(target_os = "windows")]
    grab: Option<crate::grab::Hook>,
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
        // No maximizing: a screen of another shape than the picture's would
        // need borders.
        let mut attributes = Window::default_attributes()
            .with_title(self.title.clone())
            .with_enabled_buttons(WindowButtons::CLOSE | WindowButtons::MINIMIZE);
        if let Some(monitor) = event_loop
            .primary_monitor()
            .or_else(|| event_loop.available_monitors().next())
        {
            let room = monitor.size();
            let fit =
                (ROOM * room.width as f64 / size.width as f64).min(ROOM * room.height as f64 / size.height as f64);
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
        set_icons(&window, window.scale_factor());
        self.aspect = f64::from(width.max(1)) / f64::from(height.max(1));
        #[cfg(target_os = "windows")]
        {
            crate::frame::set_aspect(self.aspect);
            crate::frame::keep_aspect(&window);
        }
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
        #[cfg(target_os = "windows")]
        {
            // Without the grab the window still works; Windows keeps its keys.
            self.grab = crate::grab::install(&window, self.proxy.clone());
        }
        self.gpu = Some(gpu);
        let inner = window.inner_size();
        self.tell(event_loop, |py, f| {
            f.call1(py, ("opened", adapter, scale, inner.width, inner.height))
        });
        self.set_cursor(event_loop);
        // Text the Mac copied before there was a loop to wake, before the
        // focus can send the local text the other way.
        self.mac_copied();
        window.request_redraw();
        Ok(())
    }

    /// Put the text the Mac copied last on the local pasteboard, if any came.
    fn mac_copied(&mut self) {
        let text = self.shared.pasteboard.lock().unwrap_or_else(|p| p.into_inner()).take();
        if let (Some(pasteboard), Some(text)) = (&mut self.pasteboard, text) {
            pasteboard.mac_copied(text);
        }
    }

    /// Take the shape and size Python asked for: the picture's aspect, never
    /// larger than the Mac can send, and the size wanted where the screen has
    /// room for it.
    fn fit(&mut self) {
        let Some(fit) = self.shared.fit.lock().unwrap_or_else(|p| p.into_inner()).take() else {
            return;
        };
        let Some(gpu) = &self.gpu else {
            return;
        };
        let window = gpu.window.clone();
        let (max_w, max_h) = fit.max;
        self.aspect = f64::from(max_w) / f64::from(max_h);
        #[cfg(target_os = "windows")]
        crate::frame::set_aspect(self.aspect);
        window.set_max_inner_size(Some(PhysicalSize::new(max_w, max_h)));
        let min_w = MIN_WIDTH.min(max_w);
        let min_h = ((f64::from(min_w) / self.aspect).round() as u32).max(1);
        window.set_min_inner_size(Some(PhysicalSize::new(min_w, min_h)));

        let mut width = f64::from(fit.want.0.min(max_w));
        let monitor = window.current_monitor();
        if let Some(monitor) = &monitor {
            let room = monitor.size();
            let height = width / self.aspect;
            let shrink = (ROOM * f64::from(room.width) / width)
                .min(ROOM * f64::from(room.height) / height)
                .min(1.0);
            width *= shrink;
        }
        let width = (width.round() as u32).max(min_w);
        let size = PhysicalSize::new(width, ((f64::from(width) / self.aspect).round() as u32).max(1));
        let _ = window.request_inner_size(size);
        if !self.placed
            && let Some(monitor) = monitor
        {
            // Centred for the size it opens at, a little high for the title bar.
            self.placed = true;
            let (room, origin) = (monitor.size(), monitor.position());
            window.set_outer_position(PhysicalPosition::new(
                origin.x + (room.width.saturating_sub(size.width) / 2) as i32,
                origin.y + (room.height.saturating_sub(size.height) / 2) as i32 / 2,
            ));
        }
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

    fn key(&mut self, event_loop: &ActiveEventLoop, keysym: u32, down: bool) {
        self.send_motion(event_loop);
        self.tell(event_loop, |py, f| f.call1(py, ("key", keysym, down)));
    }

    fn press(&mut self, event_loop: &ActiveEventLoop, key: &KeyInput) {
        match self.keyboard.press(key) {
            Some(Press::Down(keysym)) => self.key(event_loop, keysym, true),
            Some(Press::Repeat(keysym)) => {
                self.key(event_loop, keysym, false);
                self.key(event_loop, keysym, true);
            }
            None => {}
        }
    }

    fn release(&mut self, event_loop: &ActiveEventLoop, physical: PhysicalKey) {
        if let Some(keysym) = self.keyboard.release(physical) {
            self.key(event_loop, keysym, false);
        }
    }

    fn keyboard_input(&mut self, event_loop: &ActiveEventLoop, event: &KeyEvent) {
        if event.state == ElementState::Pressed {
            let base = event.key_without_modifiers();
            let key = KeyInput {
                physical: event.physical_key,
                logical: &event.logical_key,
                base: Some(&base),
                text: event.text.as_deref(),
                location: event.location,
            };
            self.press(event_loop, &key);
        } else {
            self.release(event_loop, event.physical_key);
        }
    }

    /// Keys grabbed from the system, after winit's own events.
    #[cfg(target_os = "windows")]
    fn grabbed_keys(&mut self, event_loop: &ActiveEventLoop) {
        for grabbed in crate::grab::take() {
            let physical = PhysicalKey::Code(grabbed.code);
            if grabbed.down {
                let key = KeyInput {
                    physical,
                    logical: &Key::Named(grabbed.key),
                    base: None,
                    text: None,
                    location: grabbed.location,
                };
                self.press(event_loop, &key);
            } else {
                self.release(event_loop, physical);
            }
        }
    }

    #[cfg(not(target_os = "windows"))]
    fn grabbed_keys(&mut self, _event_loop: &ActiveEventLoop) {}

    /// The window lost the focus: whatever is held comes up where the Mac
    /// cannot see it, so it is released for the Mac now.
    fn release_everything(&mut self, event_loop: &ActiveEventLoop) {
        for keysym in self.keyboard.release_all() {
            self.key(event_loop, keysym, false);
        }
        if self.pointer.buttons != 0 {
            self.pointer.buttons = 0;
            self.send_pointer(event_loop);
        }
    }

    fn send_pointer(&mut self, event_loop: &ActiveEventLoop) {
        self.pointer.moved = false;
        if let Some((x, y)) = self.pointer.position {
            let buttons = self.pointer.buttons;
            self.tell(event_loop, |py, f| f.call1(py, ("pointer", x, y, buttons)));
        }
    }

    fn send_motion(&mut self, event_loop: &ActiveEventLoop) {
        if self.pointer.moved {
            self.send_pointer(event_loop);
        }
    }

    fn scroll(&mut self, event_loop: &ActiveEventLoop, delta: MouseScrollDelta) {
        let Some((x, y)) = self.pointer.position else {
            return;
        };
        // winit's deltas are positive for left and up, the session's for
        // right and down.
        let (dx, dy) = match delta {
            MouseScrollDelta::LineDelta(dx, dy) => (-dx as f64 * POINTS_PER_NOTCH, -dy as f64 * POINTS_PER_NOTCH),
            MouseScrollDelta::PixelDelta(at) => {
                let scale = self.gpu.as_ref().map_or(1.0, |gpu| gpu.window.scale_factor());
                (-at.x / scale, -at.y / scale)
            }
        };
        // What does not make a whole point waits for the next notch.
        let (sx, sy) = (self.pointer.scroll.0 + dx, self.pointer.scroll.1 + dy);
        let whole = (sx.trunc(), sy.trunc());
        self.pointer.scroll = (sx - whole.0, sy - whole.1);
        if whole != (0.0, 0.0) {
            self.send_motion(event_loop);
            let (dx, dy) = (whole.0 as i32, whole.1 as i32);
            self.tell(event_loop, |py, f| f.call1(py, ("scroll", dx, dy, x, y)));
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
            // Taken once the events winit has are through.
            Wake::Keys => {}
            Wake::Fit => self.fit(),
            Wake::Pasteboard => self.mac_copied(),
        }
    }

    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        self.grabbed_keys(event_loop);
        self.send_motion(event_loop);
    }

    fn window_event(&mut self, event_loop: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
        match event {
            WindowEvent::CloseRequested => event_loop.exit(),
            WindowEvent::Resized(size) => {
                let Some(gpu) = &mut self.gpu else {
                    return;
                };
                gpu.resize(size.width, size.height);
                gpu.window.request_redraw();
                // Minimized, the window has no size worth telling.
                if size.width == 0 || size.height == 0 {
                    return;
                }
                #[cfg(not(target_os = "windows"))]
                if self.aspect > 0.0 {
                    // Where the drag cannot be shaped as it happens, the
                    // window is put back into shape after it.
                    let height = ((f64::from(size.width) / self.aspect).round() as u32).max(1);
                    if size.height.abs_diff(height) > 1 {
                        let _ = gpu.window.request_inner_size(PhysicalSize::new(size.width, height));
                    }
                }
                self.tell(event_loop, |py, f| f.call1(py, ("size", size.width, size.height)));
            }
            WindowEvent::ScaleFactorChanged { scale_factor, .. } => {
                if let Some(gpu) = &self.gpu {
                    set_icons(&gpu.window, scale_factor);
                }
                self.tell(event_loop, |py, f| f.call1(py, ("scale", scale_factor)))
            }
            // winit's own presses and releases around a change of focus are
            // left out: losing the focus releases everything below.
            WindowEvent::KeyboardInput {
                event,
                is_synthetic: false,
                ..
            } => {
                self.grabbed_keys(event_loop);
                self.keyboard_input(event_loop, &event);
            }
            WindowEvent::Focused(true) => {
                if let Some(text) = self.pasteboard.as_mut().and_then(Pasteboard::copied_here) {
                    self.tell(event_loop, |py, f| f.call1(py, ("pasteboard", text)));
                }
            }
            WindowEvent::Focused(false) => {
                self.grabbed_keys(event_loop);
                self.release_everything(event_loop);
            }
            WindowEvent::CursorMoved { position, .. } => {
                let at = self
                    .gpu
                    .as_ref()
                    .and_then(|gpu| gpu.picture_position(position.x, position.y));
                if at.is_some() && at != self.pointer.position {
                    self.pointer.position = at;
                    self.pointer.moved = true;
                }
            }
            WindowEvent::MouseInput { state, button, .. } => {
                let bit = match button {
                    MouseButton::Left => 0x01,
                    MouseButton::Middle => 0x02,
                    MouseButton::Right => 0x04,
                    _ => return,
                };
                match state {
                    ElementState::Pressed => self.pointer.buttons |= bit,
                    ElementState::Released => self.pointer.buttons &= !bit,
                }
                self.send_pointer(event_loop);
            }
            WindowEvent::MouseWheel { delta, .. } => self.scroll(event_loop, delta),
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
