// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! `Picture`, the Python face of a session's [`Canvas`], and `run_window`,
//! which shows one.
//!
//! The canvas sits behind a mutex rather than Python's borrow checking, so the
//! reader thread can decode with the GIL released while the window, or
//! another thread, reads the picture.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

use pyo3::create_exception;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::canvas::{Canvas, Error};
use crate::window::{self, CursorImage, Waker};

create_exception!(
    _native,
    StreamError,
    PyValueError,
    "A zlib or ZRLE rectangle broke its deflate stream: the ones after it cannot be read."
);

/// What a picture and its window share.
pub struct Shared {
    canvas: Mutex<Canvas>,
    pub waker: Waker,
    pub cursor: Mutex<Option<CursorImage>>,
    /// Frames the window has shown.
    pub frames: AtomicU64,
}

impl Shared {
    pub fn canvas(&self) -> MutexGuard<'_, Canvas> {
        // A panic while drawing leaves pixels, not broken invariants.
        self.canvas.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

/// One session's picture: a framebuffer the Mac's rectangles draw on.
#[pyclass(frozen, module = "pomona._native")]
pub struct Picture {
    shared: Arc<Shared>,
}

#[pymethods]
impl Picture {
    #[new]
    #[pyo3(signature = (width = 0, height = 0))]
    fn new(width: usize, height: usize) -> Self {
        Self {
            shared: Arc::new(Shared {
                canvas: Mutex::new(Canvas::new(width, height)),
                waker: Waker::default(),
                frames: AtomicU64::new(0),
                cursor: Mutex::new(None),
            }),
        }
    }

    #[getter]
    fn width(&self) -> usize {
        self.shared.canvas().width()
    }

    #[getter]
    fn height(&self) -> usize {
        self.shared.canvas().height()
    }

    /// Make the framebuffer black, at a new size.
    fn resize(&self, width: usize, height: usize) {
        self.shared.canvas().resize(width, height);
        self.shared.waker.picture();
    }

    /// Decode one rectangle onto the framebuffer and return whether it drew.
    /// `body` is what follows the rectangle's header, without the `u32` length
    /// its codec puts first. Raises StreamError when a zlib or ZRLE stream
    /// broke, and ValueError for any other rectangle that does not decode;
    /// whatever it drew before failing stays.
    #[allow(clippy::too_many_arguments)]
    fn decode(&self, py: Python<'_>, encoding: i32, x: u16, y: u16, w: u16, h: u16, body: &[u8]) -> PyResult<bool> {
        let result = py.detach(|| self.shared.canvas().decode(encoding, x, y, w, h, body));
        // A failed rectangle may have drawn part of itself.
        self.shared.waker.picture();
        result.map_err(|error| match error {
            Error::Stream { .. } => StreamError::new_err(error.to_string()),
            _ => PyValueError::new_err(error.to_string()),
        })
    }

    /// A copy of the framebuffer: row after row, each pixel R, G, B, A.
    fn snapshot<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let canvas = self.shared.canvas();
        let pixels = canvas.pixels();
        PyBytes::new_with(py, pixels.len() * 4, |bytes| {
            for (chunk, pixel) in bytes.as_chunks_mut::<4>().0.iter_mut().zip(pixels) {
                chunk.copy_from_slice(&pixel.to_le_bytes());
            }
            Ok(())
        })
    }

    /// The pointer's shape for a window to show: `rgba` is `width` × `height`
    /// pixels of R, G, B, A, and the hotspot is where it points.
    fn set_cursor(&self, width: u16, height: u16, hotspot_x: u16, hotspot_y: u16, rgba: Vec<u8>) -> PyResult<()> {
        if rgba.len() != usize::from(width) * usize::from(height) * 4 {
            return Err(PyValueError::new_err("the cursor's pixels do not match its size"));
        }
        *self.shared.cursor.lock().unwrap_or_else(|p| p.into_inner()) = Some(CursorImage {
            width,
            height,
            hotspot: (hotspot_x, hotspot_y),
            rgba,
        });
        self.shared.waker.cursor();
        Ok(())
    }

    /// How many frames a window has shown of this picture.
    #[getter]
    fn frames(&self) -> u64 {
        self.shared.frames.load(Ordering::Relaxed)
    }

    /// Close the window showing this picture, or keep one from opening.
    fn quit(&self) {
        self.shared.waker.quit();
    }
}

/// Show `picture` in a window until it is closed or `picture.quit()` is
/// called. `on_event(kind, *args)` hears about the window: `("opened",
/// adapter, scale)` and `("scale", scale)`. Runs on the calling thread, which
/// on macOS must be the main thread; a process can run one event loop only.
#[pyfunction]
#[pyo3(signature = (picture, on_event, title = "Pomona".to_owned()))]
pub fn run_window(py: Python<'_>, picture: &Picture, on_event: Py<PyAny>, title: String) -> PyResult<()> {
    let shared = picture.shared.clone();
    py.detach(|| window::run(shared, on_event, title))
}
