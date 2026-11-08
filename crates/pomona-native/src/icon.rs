// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! The window's icon: a monitor, drawn by code at whatever size is asked for,
//! so that no image file is kept.
//!
//! The drawing is a few rounded rectangles in units of the icon's size, each
//! pixel sampled 4×4 times for its edges.

use winit::window::Icon;

const BEZEL: [f64; 3] = rgb(0x4a, 0x4f, 0x58);
const STAND: [f64; 3] = rgb(0x8e, 0x93, 0x9c);
const SCREEN_TOP: [f64; 3] = rgb(0x5a, 0xa0, 0xea);
const SCREEN_BOTTOM: [f64; 3] = rgb(0x2f, 0x6f, 0xc4);

const SCREEN: Shape = Shape::new(0.12, 0.18, 0.88, 0.64, 0.03);
const FRAME: Shape = Shape::new(0.06, 0.12, 0.94, 0.72, 0.08);
const NECK: Shape = Shape::new(0.44, 0.71, 0.56, 0.85, 0.0);
const BASE: Shape = Shape::new(0.26, 0.84, 0.74, 0.90, 0.03);

/// Samples per pixel along each axis.
const SAMPLES: u32 = 4;

const fn rgb(r: u8, g: u8, b: u8) -> [f64; 3] {
    [r as f64 / 255.0, g as f64 / 255.0, b as f64 / 255.0]
}

/// A rectangle with rounded corners.
struct Shape {
    left: f64,
    top: f64,
    right: f64,
    bottom: f64,
    radius: f64,
}

impl Shape {
    const fn new(left: f64, top: f64, right: f64, bottom: f64, radius: f64) -> Self {
        Shape {
            left,
            top,
            right,
            bottom,
            radius,
        }
    }

    fn contains(&self, x: f64, y: f64) -> bool {
        if x < self.left || x > self.right || y < self.top || y > self.bottom {
            return false;
        }
        // Inside, unless beyond the arc of the corner it is in.
        let cx = x.clamp(self.left + self.radius, self.right - self.radius);
        let cy = y.clamp(self.top + self.radius, self.bottom - self.radius);
        (x - cx).powi(2) + (y - cy).powi(2) <= self.radius.powi(2)
    }
}

/// The colour at a point, or none where the icon is transparent.
fn colour(x: f64, y: f64) -> Option<[f64; 3]> {
    if SCREEN.contains(x, y) {
        let t = (y - SCREEN.top) / (SCREEN.bottom - SCREEN.top);
        let blend = |i: usize| SCREEN_TOP[i] + (SCREEN_BOTTOM[i] - SCREEN_TOP[i]) * t;
        Some(std::array::from_fn(blend))
    } else if FRAME.contains(x, y) {
        Some(BEZEL)
    } else if BASE.contains(x, y) || NECK.contains(x, y) {
        Some(STAND)
    } else {
        None
    }
}

/// The icon at `size`×`size` pixels, R, G, B, A in memory, not premultiplied.
pub fn rgba(size: u32) -> Vec<u8> {
    let mut out = Vec::with_capacity((size * size * 4) as usize);
    let step = 1.0 / f64::from(size * SAMPLES);
    for py in 0..size {
        for px in 0..size {
            let mut sum = [0.0; 3];
            let mut covered = 0u32;
            for sy in 0..SAMPLES {
                for sx in 0..SAMPLES {
                    let x = (f64::from(px * SAMPLES + sx) + 0.5) * step;
                    let y = (f64::from(py * SAMPLES + sy) + 0.5) * step;
                    if let Some(c) = colour(x, y) {
                        for (s, c) in sum.iter_mut().zip(c) {
                            *s += c;
                        }
                        covered += 1;
                    }
                }
            }
            if covered == 0 {
                out.extend_from_slice(&[0, 0, 0, 0]);
                continue;
            }
            let n = f64::from(covered);
            for c in sum {
                out.push((c / n * 255.0).round() as u8);
            }
            out.push((n / f64::from(SAMPLES * SAMPLES) * 255.0).round() as u8);
        }
    }
    out
}

/// The icon for winit, or none if winit takes no icon of this size.
pub fn icon(size: u32) -> Option<Icon> {
    let size = size.max(1);
    Icon::from_rgba(rgba(size), size, size).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pixel(image: &[u8], size: u32, x: u32, y: u32) -> [u8; 4] {
        let at = ((y * size + x) * 4) as usize;
        image[at..at + 4].try_into().unwrap()
    }

    #[test]
    fn draws_a_monitor() {
        let size = 32;
        let image = rgba(size);
        assert_eq!(image.len(), (size * size * 4) as usize);
        // The corners and the space beside the stand are clear.
        for (x, y) in [(0, 0), (31, 0), (0, 31), (31, 31), (3, 26)] {
            assert_eq!(pixel(&image, size, x, y)[3], 0, "({x}, {y})");
        }
        // The screen is blue and solid.
        let [r, _, b, a] = pixel(&image, size, 16, 13);
        assert_eq!(a, 255);
        assert!(b > r);
        // So are the bezel, the neck and the base.
        for (x, y) in [(16, 4), (16, 25), (16, 27)] {
            assert_eq!(pixel(&image, size, x, y)[3], 255, "({x}, {y})");
        }
    }

    #[test]
    fn makes_an_icon_at_any_size() {
        for size in [0, 1, 16, 20, 24, 32, 48, 64, 256] {
            assert!(icon(size).is_some(), "{size}");
        }
    }
}
