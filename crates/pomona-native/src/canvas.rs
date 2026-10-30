// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! The session's framebuffer, and the decoders that draw on it.
//!
//! Every codec ends up here, so the window has one buffer to show whatever the
//! Mac sends. The Adaptive decoder keeps a framebuffer of its own, because its
//! tiles copy from and refine what earlier rectangles left there; what it draws
//! is copied across rectangle by rectangle. zlib and ZRLE each keep one deflate
//! stream for the session.
//!
//! The session's pixel format is 32 bits, little endian, red at bit 16, so a
//! pixel arrives as blue, green, red and a padding byte.

use std::fmt;

use apple_adaptive::decoder::{self, Decoder};

use crate::inflate::Stream;
use crate::zrle;

pub const RAW: i32 = 0;
pub const ZLIB: i32 = 6;
pub const ZRLE: i32 = 16;
/// Apple's own 8×8 tiles.
pub const ADAPTIVE: i32 = decoder::ENCODING;

/// An opaque black pixel, R, G, B, A in memory.
const BLACK: u32 = 0xff00_0000;

/// Why a rectangle was not drawn.
#[derive(Debug)]
pub enum Error {
    /// An encoding no decoder here handles.
    Encoding(i32),
    /// A rectangle that does not lie on the framebuffer.
    Bounds {
        x: u16,
        y: u16,
        w: u16,
        h: u16,
    },
    Adaptive(decoder::Error),
    /// A zlib or ZRLE rectangle that did not inflate. Its deflate stream is
    /// lost with it.
    Stream {
        encoding: i32,
        message: String,
    },
    /// Pixels or tiles that do not fit their rectangle. The stream, if any,
    /// is still in step.
    Data {
        encoding: i32,
        message: String,
    },
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Encoding(encoding) => write!(f, "no decoder for encoding {encoding}"),
            Self::Bounds { x, y, w, h } => write!(f, "a {w}x{h} rectangle at {x},{y} off the framebuffer"),
            Self::Adaptive(error) => write!(f, "Adaptive: {error}"),
            Self::Stream { encoding, message } | Self::Data { encoding, message } => {
                write!(f, "encoding {encoding}: {message}")
            }
        }
    }
}

/// A rectangle of the framebuffer: x, y, width, height.
pub type Rect = (usize, usize, usize, usize);

/// What changed since the window last looked.
#[derive(Debug, Default, PartialEq)]
pub struct Damage {
    /// The framebuffer has a new size, and all of it is new.
    pub resized: bool,
    pub rects: Vec<Rect>,
}

/// More changed rectangles than this are merged into their union.
const MAX_RECTS: usize = 32;

/// A framebuffer of `width` × `height` pixels, row after row, each R, G, B, A
/// in memory.
pub struct Canvas {
    width: usize,
    height: usize,
    pixels: Vec<u32>,
    damage: Damage,
    adaptive: Decoder,
    zlib: Stream,
    zrle: Stream,
}

impl Canvas {
    pub fn new(width: usize, height: usize) -> Self {
        let mut canvas = Self {
            width: 0,
            height: 0,
            pixels: Vec::new(),
            damage: Damage::default(),
            adaptive: Decoder::new(),
            zlib: Stream::new(),
            zrle: Stream::new(),
        };
        canvas.resize(width, height);
        canvas
    }

    /// A black framebuffer of a new size. The decoders keep what outlives a
    /// size: the Adaptive tables and tile cache, and the deflate streams.
    pub fn resize(&mut self, width: usize, height: usize) {
        self.width = width;
        self.height = height;
        self.pixels = vec![BLACK; width * height];
        self.adaptive.resize(width, height);
        self.damage = Damage {
            resized: true,
            rects: Vec::new(),
        };
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

    /// What changed since the last call, for the window to upload.
    pub fn take_damage(&mut self) -> Damage {
        std::mem::take(&mut self.damage)
    }

    fn damaged(&mut self, (x, y, w, h): Rect) {
        let right = (x + w).min(self.width);
        let bottom = (y + h).min(self.height);
        if self.damage.resized || x >= right || y >= bottom {
            return;
        }
        let rects = &mut self.damage.rects;
        rects.push((x, y, right - x, bottom - y));
        if rects.len() > MAX_RECTS {
            let left = rects.iter().map(|r| r.0).min().unwrap_or(0);
            let top = rects.iter().map(|r| r.1).min().unwrap_or(0);
            let right = rects.iter().map(|r| r.0 + r.2).max().unwrap_or(0);
            let bottom = rects.iter().map(|r| r.1 + r.3).max().unwrap_or(0);
            *rects = vec![(left, top, right - left, bottom - top)];
        }
    }

    /// Decode one rectangle's body onto the framebuffer: for the codecs whose
    /// body starts with a `u32` length, the bytes after it. Returns whether it
    /// drew.
    pub fn decode(&mut self, encoding: i32, x: u16, y: u16, w: u16, h: u16, body: &[u8]) -> Result<bool, Error> {
        if encoding == ADAPTIVE {
            // A failed rectangle may have drawn part of itself, so it is
            // copied across either way.
            let result = self.adaptive.decode(x, y, w, h, body);
            if !matches!(result, Ok(false)) {
                self.copy_from_adaptive(x, y, w, h);
                self.damaged((usize::from(x), usize::from(y), usize::from(w), usize::from(h)));
            }
            return result.map_err(Error::Adaptive);
        }
        if !matches!(encoding, RAW | ZLIB | ZRLE) {
            return Err(Error::Encoding(encoding));
        }
        let rect = (usize::from(x), usize::from(y), usize::from(w), usize::from(h));
        // Inflated before the bounds are checked, so a stray rectangle does
        // not cost the stream the piece it carries.
        let data = match encoding {
            ZLIB => self.zlib.inflate(body, rect.2 * rect.3 * 4),
            ZRLE => self.zrle.inflate(body, rect.2 * rect.3 * 3),
            _ => Ok(body),
        }
        .map_err(|message| Error::Stream { encoding, message })?;
        if rect.0 + rect.2 > self.width || rect.1 + rect.3 > self.height {
            return Err(Error::Bounds { x, y, w, h });
        }
        let drawn = if encoding == ZRLE {
            zrle::draw(data, rect, &mut self.pixels, self.width)
        } else {
            draw_bgrx(data, rect, &mut self.pixels, self.width)
        };
        // Damaged even if the pixels failed half way: they drew that far.
        self.damaged(rect);
        drawn.map_err(|message| Error::Data { encoding, message })?;
        Ok(true)
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

/// Draw rows of blue, green, red, padding pixels.
fn draw_bgrx(
    data: &[u8],
    (x, y, w, h): (usize, usize, usize, usize),
    pixels: &mut [u32],
    stride: usize,
) -> Result<(), String> {
    if data.len() != w * h * 4 {
        return Err(format!("{} bytes of pixels for a {w}x{h} rectangle", data.len()));
    }
    if w == 0 {
        return Ok(());
    }
    for (row, line) in data.chunks_exact(w * 4).enumerate() {
        let start = (y + row) * stride + x;
        for (pixel, bgrx) in pixels[start..start + w].iter_mut().zip(line.as_chunks::<4>().0) {
            *pixel = zrle::rgba(bgrx[0], bgrx[1], bgrx[2]);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::inflate::tests::deflate_pieces;

    const RED: u32 = 0xff00_00ff;
    const GREEN: u32 = 0xff00_ff00;

    #[test]
    fn drawing_records_damage_and_a_resize_replaces_it() {
        let mut canvas = Canvas::new(4, 4);
        assert!(canvas.take_damage().resized);
        canvas.decode(RAW, 1, 1, 1, 1, &[0; 4]).unwrap();
        assert_eq!(
            canvas.take_damage(),
            Damage {
                resized: false,
                rects: vec![(1, 1, 1, 1)]
            }
        );
        assert_eq!(canvas.take_damage(), Damage::default());
        canvas.decode(RAW, 1, 1, 1, 1, &[0; 4]).unwrap();
        canvas.resize(2, 2);
        assert_eq!(
            canvas.take_damage(),
            Damage {
                resized: true,
                rects: vec![]
            }
        );
    }

    #[test]
    fn too_many_damaged_rectangles_merge_into_their_union() {
        let mut canvas = Canvas::new(4, 4);
        canvas.take_damage();
        canvas.decode(RAW, 3, 3, 1, 1, &[0; 4]).unwrap();
        for _ in 0..MAX_RECTS {
            canvas.decode(RAW, 0, 1, 1, 1, &[0; 4]).unwrap();
        }
        assert_eq!(canvas.take_damage().rects, vec![(0, 1, 4, 3)]);
    }

    #[test]
    fn a_new_canvas_is_opaque_black() {
        let canvas = Canvas::new(3, 2);
        assert_eq!(canvas.pixels(), &[BLACK; 6]);
    }

    #[test]
    fn an_unknown_encoding_is_refused() {
        let mut canvas = Canvas::new(8, 8);
        assert!(matches!(canvas.decode(7, 0, 0, 8, 8, &[]), Err(Error::Encoding(7))));
    }

    #[test]
    fn a_picture_before_the_tables_is_refused() {
        let mut canvas = Canvas::new(8, 8);
        let result = canvas.decode(ADAPTIVE, 0, 0, 8, 8, &[0, 0, 0, 0, 0, 0, 0, 0]);
        assert!(matches!(result, Err(Error::Adaptive(decoder::Error::NoTables))));
    }

    #[test]
    fn raw_pixels_arrive_as_blue_green_red_padding() {
        let mut canvas = Canvas::new(3, 2);
        // A red and a green pixel at 1,1; the padding byte is ignored.
        canvas.decode(RAW, 1, 1, 2, 1, &[0, 0, 255, 7, 0, 255, 0, 7]).unwrap();
        assert_eq!(canvas.pixels(), &[BLACK, BLACK, BLACK, BLACK, RED, GREEN]);
    }

    #[test]
    fn zlib_rectangles_share_one_stream() {
        let first = [0u8, 0, 255, 0].repeat(4); // 2x2 red
        let second = [0u8, 255, 0, 0].repeat(2); // 2x1 green
        let pieces = deflate_pieces(&[&first, &second]);
        let mut canvas = Canvas::new(2, 2);
        canvas.decode(ZLIB, 0, 0, 2, 2, &pieces[0]).unwrap();
        canvas.decode(ZLIB, 0, 1, 2, 1, &pieces[1]).unwrap();
        assert_eq!(canvas.pixels(), &[RED, RED, GREEN, GREEN]);
    }

    #[test]
    fn zrle_rectangles_share_one_stream_of_their_own() {
        let solid_red = [1u8, 0, 0, 255];
        let zrle = deflate_pieces(&[&solid_red, &solid_red]);
        let zlib = deflate_pieces(&[&[0u8, 255, 0, 0]]);
        let mut canvas = Canvas::new(2, 1);
        canvas.decode(ZRLE, 0, 0, 1, 1, &zrle[0]).unwrap();
        // A zlib rectangle in between does not disturb ZRLE's stream.
        canvas.decode(ZLIB, 1, 0, 1, 1, &zlib[0]).unwrap();
        canvas.decode(ZRLE, 0, 0, 1, 1, &zrle[1]).unwrap();
        assert_eq!(canvas.pixels(), &[RED, GREEN]);
    }

    #[test]
    fn a_rectangle_off_the_framebuffer_still_feeds_the_stream() {
        let pixel = [0u8, 0, 255, 0];
        let pieces = deflate_pieces(&[&pixel, &pixel]);
        let mut canvas = Canvas::new(1, 1);
        assert!(matches!(
            canvas.decode(ZLIB, 1, 0, 1, 1, &pieces[0]),
            Err(Error::Bounds { .. })
        ));
        canvas.decode(ZLIB, 0, 0, 1, 1, &pieces[1]).unwrap();
        assert_eq!(canvas.pixels(), &[RED]);
    }

    #[test]
    fn the_wrong_amount_of_pixels_is_refused() {
        let mut canvas = Canvas::new(2, 2);
        assert!(matches!(
            canvas.decode(RAW, 0, 0, 2, 2, &[0; 12]),
            Err(Error::Data { .. })
        ));
    }
}
