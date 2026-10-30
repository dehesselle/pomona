// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! `Picture`, the Python face of a session's [`Canvas`].
//!
//! The canvas sits behind a mutex rather than Python's borrow checking, so the
//! reader thread can decode with the GIL released while another thread reads
//! the picture.

use std::sync::{Arc, Mutex, MutexGuard};

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::canvas::Canvas;

/// One session's picture: a framebuffer the Mac's rectangles draw on.
#[pyclass(frozen, module = "pomona._native")]
pub struct Picture {
    canvas: Arc<Mutex<Canvas>>,
}

impl Picture {
    fn canvas(&self) -> MutexGuard<'_, Canvas> {
        // A panic while drawing leaves pixels, not broken invariants.
        self.canvas.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}

#[pymethods]
impl Picture {
    #[new]
    #[pyo3(signature = (width = 0, height = 0))]
    fn new(width: usize, height: usize) -> Self {
        Self {
            canvas: Arc::new(Mutex::new(Canvas::new(width, height))),
        }
    }

    #[getter]
    fn width(&self) -> usize {
        self.canvas().width()
    }

    #[getter]
    fn height(&self) -> usize {
        self.canvas().height()
    }

    /// Make the framebuffer black, at a new size.
    fn resize(&self, width: usize, height: usize) {
        self.canvas().resize(width, height);
    }

    /// Decode one rectangle onto the framebuffer and return whether it drew.
    /// `body` is what follows the rectangle's header, without the `u32` length
    /// its codec puts first. Raises ValueError for a rectangle that does not
    /// decode; whatever it drew before failing stays.
    #[allow(clippy::too_many_arguments)]
    fn decode(&self, py: Python<'_>, encoding: i32, x: u16, y: u16, w: u16, h: u16, body: &[u8]) -> PyResult<bool> {
        py.detach(|| self.canvas().decode(encoding, x, y, w, h, body))
            .map_err(|error| PyValueError::new_err(error.to_string()))
    }

    /// A copy of the framebuffer: row after row, each pixel R, G, B, A.
    fn snapshot<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let canvas = self.canvas();
        let pixels = canvas.pixels();
        PyBytes::new_with(py, pixels.len() * 4, |bytes| {
            for (chunk, pixel) in bytes.as_chunks_mut::<4>().0.iter_mut().zip(pixels) {
                chunk.copy_from_slice(&pixel.to_le_bytes());
            }
            Ok(())
        })
    }
}
