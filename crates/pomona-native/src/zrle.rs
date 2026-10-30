// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! ZRLE's tiles, from a rectangle's inflated bytes onto the canvas.
//!
//! The rectangle is cut into 64×64 tiles from its own origin, left to right and
//! top to bottom, the last ones clipped. Each tile starts with a subencoding:
//! raw, solid, a packed palette, plain run-length, or palette run-length. A
//! pixel is three bytes, the low three of the session's little-endian format:
//! blue, green, red. A Mac sends raw tiles only, but every subencoding is read.

const TILE: usize = 64;

/// A pixel as the canvas holds it, from blue, green and red.
pub fn rgba(blue: u8, green: u8, red: u8) -> u32 {
    u32::from_le_bytes([red, green, blue, 0xff])
}

struct Reader<'a> {
    data: &'a [u8],
    at: usize,
}

impl Reader<'_> {
    fn u8(&mut self) -> Result<u8, String> {
        let byte = *self.data.get(self.at).ok_or("ZRLE data ended inside a tile")?;
        self.at += 1;
        Ok(byte)
    }

    fn bytes(&mut self, count: usize) -> Result<&[u8], String> {
        let bytes = self
            .data
            .get(self.at..self.at + count)
            .ok_or("ZRLE data ended inside a tile")?;
        self.at += count;
        Ok(bytes)
    }

    fn pixel(&mut self) -> Result<u32, String> {
        let p = self.bytes(3)?;
        Ok(rgba(p[0], p[1], p[2]))
    }

    fn palette(&mut self, size: usize) -> Result<Vec<u32>, String> {
        (0..size).map(|_| self.pixel()).collect()
    }

    /// A run: 1 plus every byte up to and including the first that is not 255.
    fn run(&mut self) -> Result<usize, String> {
        let mut length = 1;
        loop {
            let byte = self.u8()?;
            length += usize::from(byte);
            if byte != 255 {
                return Ok(length);
            }
        }
    }
}

/// Where a tile goes: its pixels, row after row, are written through `put`.
struct Tile<'a> {
    pixels: &'a mut [u32],
    stride: usize,
    x: usize,
    y: usize,
    w: usize,
    h: usize,
}

impl Tile<'_> {
    fn put(&mut self, index: usize, pixel: u32) {
        let (row, column) = (index / self.w, index % self.w);
        self.pixels[(self.y + row) * self.stride + self.x + column] = pixel;
    }

    fn area(&self) -> usize {
        self.w * self.h
    }
}

/// Draw a rectangle at `x`, `y` of `w` × `h`, already checked to lie on the
/// canvas, from its inflated `data`.
pub fn draw(
    data: &[u8],
    (x, y, w, h): (usize, usize, usize, usize),
    pixels: &mut [u32],
    stride: usize,
) -> Result<(), String> {
    let mut reader = Reader { data, at: 0 };
    for ty in (0..h).step_by(TILE) {
        for tx in (0..w).step_by(TILE) {
            let mut tile = Tile {
                pixels: &mut *pixels,
                stride,
                x: x + tx,
                y: y + ty,
                w: TILE.min(w - tx),
                h: TILE.min(h - ty),
            };
            draw_tile(&mut reader, &mut tile)?;
        }
    }
    if reader.at != data.len() {
        return Err(format!(
            "{} bytes of ZRLE data after the last tile",
            data.len() - reader.at
        ));
    }
    Ok(())
}

fn draw_tile(reader: &mut Reader<'_>, tile: &mut Tile<'_>) -> Result<(), String> {
    let area = tile.area();
    match reader.u8()? {
        0 => {
            for index in 0..area {
                let pixel = reader.pixel()?;
                tile.put(index, pixel);
            }
        }
        1 => {
            let pixel = reader.pixel()?;
            for index in 0..area {
                tile.put(index, pixel);
            }
        }
        size @ 2..=16 => {
            let palette = reader.palette(usize::from(size))?;
            let bits = match size {
                2 => 1,
                3..=4 => 2,
                _ => 4,
            };
            // Each row starts on a byte, most significant bits first.
            let row_bytes = (tile.w * bits).div_ceil(8);
            for row in 0..tile.h {
                let packed = reader.bytes(row_bytes)?;
                for column in 0..tile.w {
                    let bit = column * bits;
                    let index = (packed[bit / 8] >> (8 - bits - bit % 8)) & ((1 << bits) - 1);
                    let pixel = *palette
                        .get(usize::from(index))
                        .ok_or("a ZRLE palette index past the palette")?;
                    tile.put(row * tile.w + column, pixel);
                }
            }
        }
        128 => {
            let mut index = 0;
            while index < area {
                let pixel = reader.pixel()?;
                index = fill(tile, index, reader.run()?, pixel)?;
            }
        }
        size @ 130..=255 => {
            let palette = reader.palette(usize::from(size - 128))?;
            let mut index = 0;
            while index < area {
                let entry = reader.u8()?;
                let pixel = *palette
                    .get(usize::from(entry & 0x7f))
                    .ok_or("a ZRLE palette index past the palette")?;
                let length = if entry & 0x80 != 0 { reader.run()? } else { 1 };
                index = fill(tile, index, length, pixel)?;
            }
        }
        other => return Err(format!("ZRLE subencoding {other}, which is unused")),
    }
    Ok(())
}

fn fill(tile: &mut Tile<'_>, from: usize, length: usize, pixel: u32) -> Result<usize, String> {
    let to = from + length;
    if to > tile.area() {
        return Err("a ZRLE run past the end of its tile".to_owned());
    }
    for index in from..to {
        tile.put(index, pixel);
    }
    Ok(to)
}

#[cfg(test)]
mod tests {
    use super::*;

    const RED: [u8; 3] = [0, 0, 255];
    const BLUE: [u8; 3] = [255, 0, 0];

    fn canvas(w: usize, h: usize, data: &[u8]) -> Result<Vec<u32>, String> {
        let mut pixels = vec![0; w * h];
        draw(data, (0, 0, w, h), &mut pixels, w)?;
        Ok(pixels)
    }

    #[test]
    fn a_solid_tile_fills_it() {
        let data = [&[1u8][..], &RED].concat();
        assert_eq!(canvas(3, 2, &data).unwrap(), vec![rgba(0, 0, 255); 6]);
    }

    #[test]
    fn a_raw_tile_is_its_pixels_in_rows() {
        let data = [&[0u8][..], &RED, &BLUE, &BLUE, &RED].concat();
        let red = rgba(0, 0, 255);
        let blue = rgba(255, 0, 0);
        assert_eq!(canvas(2, 2, &data).unwrap(), vec![red, blue, blue, red]);
    }

    #[test]
    fn a_wide_rectangle_is_cut_into_clipped_tiles() {
        // 70 wide: a 64-wide solid red tile, then a 6-wide solid blue one.
        let data = [&[1u8][..], &RED, &[1], &BLUE].concat();
        let pixels = canvas(70, 1, &data).unwrap();
        assert!(pixels[..64].iter().all(|&p| p == rgba(0, 0, 255)));
        assert!(pixels[64..].iter().all(|&p| p == rgba(255, 0, 0)));
    }

    #[test]
    fn a_packed_palette_starts_each_row_on_a_byte() {
        // Two colours, one bit each: rows 1,0,1 and 0,1,0, each padded.
        let data = [&[2u8][..], &RED, &BLUE, &[0b1010_0000, 0b0100_0000]].concat();
        let (r, b) = (rgba(0, 0, 255), rgba(255, 0, 0));
        assert_eq!(canvas(3, 2, &data).unwrap(), vec![b, r, b, r, b, r]);
    }

    #[test]
    fn plain_rle_counts_255_as_more_to_come() {
        // One 16x17 tile: red for 1 + 255 + 4 = 260 pixels, then blue for 1 + 11.
        let data = [&[128u8][..], &RED, &[255, 4], &BLUE, &[11]].concat();
        let pixels = canvas(16, 17, &data).unwrap();
        assert!(pixels[..260].iter().all(|&p| p == rgba(0, 0, 255)));
        assert!(pixels[260..].iter().all(|&p| p == rgba(255, 0, 0)));
    }

    #[test]
    fn palette_rle_mixes_runs_and_single_pixels() {
        let data = [&[130u8][..], &RED, &BLUE, &[0x80, 1, 1, 0]].concat();
        let (r, b) = (rgba(0, 0, 255), rgba(255, 0, 0));
        assert_eq!(canvas(4, 1, &data).unwrap(), vec![r, r, b, r]);
    }

    #[test]
    fn short_or_overlong_data_is_refused() {
        assert!(canvas(2, 2, &[0, 1, 2, 3]).is_err());
        assert!(canvas(1, 1, &[1, 0, 0, 0, 9]).is_err());
        assert!(canvas(2, 1, &[128, 0, 0, 0, 5]).is_err());
        assert!(canvas(1, 1, &[17]).is_err());
    }
}
