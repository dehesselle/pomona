// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! The session's framebuffer, and the decoders that draw on it.
//!
//! Every codec ends up here, so the window has one buffer to show whatever the
//! Mac sends. The Adaptive decoder keeps a framebuffer of its own, because its
//! tiles copy from and refine what earlier rectangles left there; what it draws
//! is copied across rectangle by rectangle.

use std::fmt;

use apple_adaptive::decoder::{self, Decoder};

/// Apple's own 8×8 tiles.
pub const ADAPTIVE: i32 = decoder::ENCODING;

/// An opaque black pixel, R, G, B, A in memory.
const BLACK: u32 = 0xff00_0000;

/// Why a rectangle was not drawn.
#[derive(Debug)]
pub enum Error {
    /// An encoding no decoder here handles.
    Encoding(i32),
    Adaptive(decoder::Error),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Encoding(encoding) => write!(f, "no decoder for encoding {encoding}"),
            Self::Adaptive(error) => write!(f, "Adaptive: {error}"),
        }
    }
}

/// A framebuffer of `width` × `height` pixels, row after row, each R, G, B, A
/// in memory.
pub struct Canvas {
    width: usize,
    height: usize,
    pixels: Vec<u32>,
    adaptive: Decoder,
}

impl Canvas {
    pub fn new(width: usize, height: usize) -> Self {
        let mut canvas = Self {
            width: 0,
            height: 0,
            pixels: Vec::new(),
            adaptive: Decoder::new(),
        };
        canvas.resize(width, height);
        canvas
    }

    /// A black framebuffer of a new size. The decoders keep what outlives a
    /// size, such as the Adaptive tables and tile cache.
    pub fn resize(&mut self, width: usize, height: usize) {
        self.width = width;
        self.height = height;
        self.pixels = vec![BLACK; width * height];
        self.adaptive.resize(width, height);
    }

    pub fn width(&self) -> usize {
        self.width
    }

    pub fn height(&self) -> usize {
        self.height
    }

    pub fn pixels(&self) -> &[u32] {
        &self.pixels
    }

    /// Decode one rectangle's body onto the framebuffer: for the codecs whose
    /// body starts with a `u32` length, the bytes after it. Returns whether it
    /// drew.
    pub fn decode(&mut self, encoding: i32, x: u16, y: u16, w: u16, h: u16, body: &[u8]) -> Result<bool, Error> {
        match encoding {
            ADAPTIVE => {
                // A failed rectangle may have drawn part of itself, so it is
                // copied across either way.
                let result = self.adaptive.decode(x, y, w, h, body);
                if !matches!(result, Ok(false)) {
                    self.copy_from_adaptive(x, y, w, h);
                }
                result.map_err(Error::Adaptive)
            }
            other => Err(Error::Encoding(other)),
        }
    }

    /// Copy a rectangle of the Adaptive decoder's framebuffer, clipped to the
    /// canvas, onto the canvas.
    fn copy_from_adaptive(&mut self, x: u16, y: u16, w: u16, h: u16) {
        let (x, y) = (usize::from(x), usize::from(y));
        let right = (x + usize::from(w)).min(self.width);
        let bottom = (y + usize::from(h)).min(self.height);
        if x >= right || y >= bottom {
            return;
        }
        let source = self.adaptive.pixels();
        for row in y..bottom {
            let span = row * self.width + x..row * self.width + right;
            self.pixels[span.clone()].copy_from_slice(&source[span]);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_new_canvas_is_opaque_black() {
        let canvas = Canvas::new(3, 2);
        assert_eq!(canvas.pixels(), &[BLACK; 6]);
    }

    #[test]
    fn an_unknown_encoding_is_refused() {
        let mut canvas = Canvas::new(8, 8);
        assert!(matches!(canvas.decode(6, 0, 0, 8, 8, &[]), Err(Error::Encoding(6))));
    }

    #[test]
    fn a_picture_before_the_tables_is_refused() {
        let mut canvas = Canvas::new(8, 8);
        let result = canvas.decode(ADAPTIVE, 0, 0, 8, 8, &[0, 0, 0, 0, 0, 0, 0, 0]);
        assert!(matches!(result, Err(Error::Adaptive(decoder::Error::NoTables))));
    }
}
