// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! Grabbing the keys Windows would act on itself, while the window is in
//! front: the Windows keys, Alt-Tab, Alt-Esc, Alt-Space and Ctrl-Esc.
//!
//! A low-level keyboard hook sees every key before Windows does. It swallows
//! these and queues them for the window, which takes them after the events
//! winit already has: a hook runs ahead of the input it hooks, so Alt's own
//! event may still be waiting when Tab is grabbed, and the Mac must see Alt
//! first. Every other key goes its usual way, through winit and the layout.
//!
//! The hook runs on a thread of its own that does nothing else: Windows
//! waits for a hook only so long before it passes the key on unhooked.

use std::sync::{Mutex, mpsc};
use std::thread::JoinHandle;

use windows_sys::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::System::Threading::GetCurrentThreadId;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    GetAsyncKeyState, VK_CONTROL, VK_ESCAPE, VK_LWIN, VK_RWIN, VK_SPACE, VK_TAB,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, GetForegroundWindow, GetMessageW, HC_ACTION, KBDLLHOOKSTRUCT, LLKHF_ALTDOWN, MSG,
    PostThreadMessageW, SetWindowsHookExW, UnhookWindowsHookEx, WH_KEYBOARD_LL, WM_KEYDOWN, WM_QUIT, WM_SYSKEYDOWN,
};
use winit::event_loop::EventLoopProxy;
use winit::keyboard::{KeyCode, KeyLocation, NamedKey};
use winit::raw_window_handle::{HasWindowHandle, RawWindowHandle};
use winit::window::Window;

use crate::window::Wake;

/// A key the hook took from Windows.
pub struct Grabbed {
    pub code: KeyCode,
    pub key: NamedKey,
    pub location: KeyLocation,
    pub down: bool,
}

struct Target {
    window: usize,
    proxy: EventLoopProxy<Wake>,
    /// Keys grabbed down: their release is grabbed too, wherever Alt is.
    held: Vec<u32>,
    queue: Vec<Grabbed>,
}

static TARGET: Mutex<Option<Target>> = Mutex::new(None);

/// The hook's thread, while the hook is installed.
pub struct Hook {
    thread: u32,
    handle: Option<JoinHandle<()>>,
}

impl Drop for Hook {
    fn drop(&mut self) {
        // SAFETY: a message to a thread of ours, which ends its loop.
        unsafe { PostThreadMessageW(self.thread, WM_QUIT, 0, 0) };
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
        *target() = None;
    }
}

fn target() -> std::sync::MutexGuard<'static, Option<Target>> {
    TARGET.lock().unwrap_or_else(|p| p.into_inner())
}

/// Grab the keys for `window`, waking its loop through `proxy`. None if
/// Windows refuses.
pub fn install(window: &Window, proxy: EventLoopProxy<Wake>) -> Option<Hook> {
    let RawWindowHandle::Win32(handle) = window.window_handle().ok()?.as_raw() else {
        return None;
    };
    *target() = Some(Target {
        window: handle.hwnd.get() as usize,
        proxy,
        held: Vec::new(),
        queue: Vec::new(),
    });
    let (started, start) = mpsc::channel();
    let handle = std::thread::Builder::new()
        .name("pomona-keys".into())
        .spawn(move || run(started))
        .ok();
    match (handle, start.recv()) {
        (Some(handle), Ok(Some(thread))) => Some(Hook {
            thread,
            handle: Some(handle),
        }),
        (handle, _) => {
            if let Some(handle) = handle {
                let _ = handle.join();
            }
            *target() = None;
            None
        }
    }
}

/// The hook's thread: install it, then answer Windows until told to stop.
fn run(started: mpsc::Sender<Option<u32>>) {
    // SAFETY: a low-level hook needs a module handle but runs in this
    // process; the procedure lives as long as the extension.
    let hook = unsafe { SetWindowsHookExW(WH_KEYBOARD_LL, Some(hook), GetModuleHandleW(std::ptr::null()), 0) };
    if hook.is_null() {
        let _ = started.send(None);
        return;
    }
    // SAFETY: plain Win32 calls; the message is only written by GetMessageW.
    unsafe {
        let _ = started.send(Some(GetCurrentThreadId()));
        let mut message: MSG = std::mem::zeroed();
        // Windows calls the hook from inside GetMessageW.
        while GetMessageW(&mut message, std::ptr::null_mut(), 0, 0) > 0 {}
        UnhookWindowsHookEx(hook);
    }
}

/// The keys grabbed since the last call, in order.
pub fn take() -> Vec<Grabbed> {
    target()
        .as_mut()
        .map(|t| std::mem::take(&mut t.queue))
        .unwrap_or_default()
}

fn grabs(vk: u16, alt: bool, ctrl: bool) -> Option<(KeyCode, NamedKey, KeyLocation)> {
    Some(match vk {
        VK_LWIN => (KeyCode::SuperLeft, NamedKey::Super, KeyLocation::Left),
        VK_RWIN => (KeyCode::SuperRight, NamedKey::Super, KeyLocation::Right),
        VK_TAB if alt => (KeyCode::Tab, NamedKey::Tab, KeyLocation::Standard),
        VK_ESCAPE if alt || ctrl => (KeyCode::Escape, NamedKey::Escape, KeyLocation::Standard),
        VK_SPACE if alt => (KeyCode::Space, NamedKey::Space, KeyLocation::Standard),
        _ => return None,
    })
}

unsafe extern "system" fn hook(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code == HC_ACTION as i32 {
        // SAFETY: for HC_ACTION, lparam points to the event's KBDLLHOOKSTRUCT.
        let info = unsafe { &*(lparam as *const KBDLLHOOKSTRUCT) };
        if grab(info, wparam) {
            return 1;
        }
    }
    // SAFETY: passing the event on, as every hook must that does not take it.
    unsafe { CallNextHookEx(std::ptr::null_mut(), code, wparam, lparam) }
}

/// Whether the hook takes this event from Windows.
fn grab(info: &KBDLLHOOKSTRUCT, wparam: WPARAM) -> bool {
    let mut guard = target();
    let Some(target) = guard.as_mut() else {
        return false;
    };
    let down = matches!(wparam as u32, WM_KEYDOWN | WM_SYSKEYDOWN);
    let vk = info.vkCode;
    let held = target.held.iter().position(|&k| k == vk);
    if !down && held.is_none() {
        return false;
    }
    // SAFETY: plain Win32 queries without arguments to check.
    let in_front = unsafe { GetForegroundWindow() } as usize == target.window;
    let ctrl = unsafe { GetAsyncKeyState(VK_CONTROL as i32) } < 0;
    if down && !in_front {
        return false;
    }
    let alt = info.flags & LLKHF_ALTDOWN != 0;
    let Some((code, key, location)) = grabs(vk as u16, alt, ctrl).or_else(|| {
        // A release of a key grabbed down, with Alt gone meanwhile.
        held.and_then(|_| grabs(vk as u16, true, true))
    }) else {
        return false;
    };
    match (down, held) {
        (true, None) => target.held.push(vk),
        (false, Some(at)) => {
            target.held.swap_remove(at);
        }
        _ => {}
    }
    target.queue.push(Grabbed {
        code,
        key,
        location,
        down,
    });
    let _ = target.proxy.send_event(Wake::Keys);
    true
}
