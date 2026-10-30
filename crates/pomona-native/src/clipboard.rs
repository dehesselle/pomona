// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! The local pasteboard, text only, as the window shares it with the Mac.
//!
//! Text the Mac copies is put on the local pasteboard at once. Text copied
//! locally goes to the Mac when the window gets the focus, which is when it
//! can matter there. The text last exchanged either way is remembered, so
//! nothing goes back where it came from: the Mac reports text it was given as
//! a change of its own.

use arboard::Clipboard;

#[derive(Default)]
pub struct Pasteboard {
    /// None until first used, and when the system has none to give.
    clipboard: Option<Clipboard>,
    last: Option<String>,
}

impl Pasteboard {
    fn clipboard(&mut self) -> Option<&mut Clipboard> {
        if self.clipboard.is_none() {
            self.clipboard = Clipboard::new().ok();
        }
        self.clipboard.as_mut()
    }

    /// Put the Mac's text on the local pasteboard, unless it came from there.
    pub fn mac_copied(&mut self, text: String) {
        if self.last.as_ref() == Some(&text) {
            return;
        }
        // A pasteboard that refuses keeps what it had; the next copy on the
        // Mac tries again.
        if let Some(clipboard) = self.clipboard()
            && clipboard.set_text(text.as_str()).is_ok()
        {
            self.last = Some(text);
        }
    }

    /// The local text, if it changed since it was last exchanged.
    pub fn copied_here(&mut self) -> Option<String> {
        let text = self.clipboard()?.get_text().ok()?;
        if self.last.as_ref() == Some(&text) {
            return None;
        }
        self.last = Some(text.clone());
        Some(text)
    }
}
