// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! Keeping the window's shape the picture's while it is dragged (Windows).
//!
//! Windows asks the window, with `WM_SIZING`, before every step of a drag
//! how large it may become. The window's procedure is subclassed to answer
//! with the picture's aspect, so the edges follow it and no border opens up.
//! winit has no such setting; resizing back after the fact would flicker.

use std::sync::atomic::{AtomicU64, Ordering};

use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows_sys::Win32::UI::Shell::{DefSubclassProc, SetWindowSubclass};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    GetClientRect, GetWindowRect, WM_SIZING, WMSZ_BOTTOM, WMSZ_TOP, WMSZ_TOPLEFT, WMSZ_TOPRIGHT,
};
use winit::raw_window_handle::{HasWindowHandle, RawWindowHandle};
use winit::window::Window;

/// Width over height of the picture, as f64 bits; 0 while unknown.
static ASPECT: AtomicU64 = AtomicU64::new(0);

/// Keep `window` at the aspect `set_aspect` gives. False if Windows refuses.
pub fn keep_aspect(window: &Window) -> bool {
    let Ok(handle) = window.window_handle() else {
        return false;
    };
    let RawWindowHandle::Win32(handle) = handle.as_raw() else {
        return false;
    };
    // SAFETY: the window is ours and outlives the subclass, which Windows
    // removes with it.
    unsafe { SetWindowSubclass(handle.hwnd.get() as HWND, Some(subclass), 1, 0) != 0 }
}

pub fn set_aspect(aspect: f64) {
    ASPECT.store(aspect.to_bits(), Ordering::Relaxed);
}

unsafe extern "system" fn subclass(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    _data: usize,
) -> LRESULT {
    let aspect = f64::from_bits(ASPECT.load(Ordering::Relaxed));
    if message == WM_SIZING && aspect > 0.0 {
        // SAFETY: for WM_SIZING, lparam points to the window rectangle being
        // dragged, which the procedure may change.
        let rect = unsafe { &mut *(lparam as *mut RECT) };
        // SAFETY: plain queries of our own window.
        let (frame_w, frame_h) = unsafe { frame(hwnd) };
        let width = f64::from(rect.right - rect.left - frame_w);
        let height = f64::from(rect.bottom - rect.top - frame_h);
        let height_for_width = (width / aspect).round() as i32 + frame_h;
        let width_for_height = (height * aspect).round() as i32 + frame_w;
        match wparam as u32 {
            WMSZ_TOP | WMSZ_BOTTOM => rect.right = rect.left + width_for_height,
            WMSZ_TOPLEFT | WMSZ_TOPRIGHT => rect.top = rect.bottom - height_for_width,
            // The sides (WMSZ_LEFT, WMSZ_RIGHT) and the bottom corners: the
            // height follows the width.
            _ => rect.bottom = rect.top + height_for_width,
        }
        return 1;
    }
    // SAFETY: everything else goes on to winit's procedure.
    unsafe { DefSubclassProc(hwnd, message, wparam, lparam) }
}

/// The window's frame: its outer size less its client area.
unsafe fn frame(hwnd: HWND) -> (i32, i32) {
    let mut window = RECT {
        left: 0,
        top: 0,
        right: 0,
        bottom: 0,
    };
    let mut client = window;
    // SAFETY: both rectangles are ours to write.
    unsafe {
        GetWindowRect(hwnd, &mut window);
        GetClientRect(hwnd, &mut client);
    }
    (
        (window.right - window.left) - client.right,
        (window.bottom - window.top) - client.bottom,
    )
}
