// SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
//
// SPDX-License-Identifier: MIT

//! Keys as the Mac reads them: winit's key events become X11 keysyms.
//!
//! A key sends the character the local layout made of it, so a German
//! keyboard types what its caps show; the Mac finds that character on its own
//! layout. The Mac's own rules, measured by zDesk on macOS 26:
//!
//! - Modifiers follow the keysym: an uppercase letter brings Shift, and Shift
//!   is stripped from a lowercase one. Under Control or Command a letter
//!   therefore goes out in the case Shift gives it, whatever Caps Lock says,
//!   or Command-Z under Caps Lock would arrive as Command-Shift-Z.
//! - `Meta` lands on Option, and `Alt` and `Super` both on Command. A PC's Alt
//!   goes out as Meta, its Windows key as Super; a Mac's Option and Command
//!   come from winit as Alt and Super and go out the same way.
//! - Insert, Pause, Scroll Lock, Print and Menu have nothing on the Mac, and
//!   Num Lock would arrive as Keypad Clear: none of them is sent. Neither is
//!   Caps Lock: the local layout has applied it already.
//!
//! And Pomona's own, measured on macOS 26.6 with a US layout:
//!
//! - Every character beyond ASCII goes out as X11's Unicode keysym. The Mac
//!   types those whatever its layout (ä, ß, €), but typed a Latin-1 keysym
//!   such as `0xe4` for ä as a backslash, or not at all.
//! - The Mac neither repeats a held key nor types again for a second press
//!   without a release. A repeat therefore goes out as a release and a press;
//!   a held modifier's repeats are dropped.

use std::collections::HashMap;

use winit::keyboard::{Key, KeyLocation, NamedKey, PhysicalKey};

const SHIFT: (u32, u32) = (0xffe1, 0xffe2);
const CONTROL: (u32, u32) = (0xffe3, 0xffe4);
/// Option on the Mac.
const META: (u32, u32) = (0xffe7, 0xffe8);
/// Command on the Mac.
const SUPER: (u32, u32) = (0xffeb, 0xffec);
/// X11's keysyms for Unicode.
const UNICODE: u32 = 0x0100_0000;

/// What a press sends.
#[derive(Debug, PartialEq, Eq)]
pub enum Press {
    /// The key goes down.
    Down(u32),
    /// The key, held, repeats: it goes up and down again.
    Repeat(u32),
}

/// One key event, as winit reports it.
pub struct KeyInput<'a> {
    pub physical: PhysicalKey,
    /// The key under every modifier but Control.
    pub logical: &'a Key,
    /// The key under no modifier at all, where the platform says.
    pub base: Option<&'a Key>,
    /// The text the press made, dead keys composed in.
    pub text: Option<&'a str>,
    pub location: KeyLocation,
}

/// The keys held down, and what each press sent.
#[derive(Default)]
pub struct Keyboard {
    /// Send Control as Command and the Windows key as Control.
    pub ctrl_as_command: bool,
    held: HashMap<PhysicalKey, u32>,
}

impl Keyboard {
    pub fn new(ctrl_as_command: bool) -> Self {
        Keyboard {
            ctrl_as_command,
            held: HashMap::new(),
        }
    }

    /// What a press sends, if the key has a keysym for the Mac. A repeat
    /// sends what the first press did.
    pub fn press(&mut self, key: &KeyInput) -> Option<Press> {
        if let Some(&keysym) = self.held.get(&key.physical) {
            let modifier = [SHIFT, CONTROL, META, SUPER]
                .iter()
                .any(|pair| keysym == pair.0 || keysym == pair.1);
            return (!modifier).then_some(Press::Repeat(keysym));
        }
        let keysym = match key.logical {
            Key::Named(named) => self.named(*named, key.location),
            _ => self.character(key).and_then(|c| character_keysym(c, key.location)),
        }?;
        self.held.insert(key.physical, keysym);
        Some(Press::Down(keysym))
    }

    /// The keysym a release sends: its press's, if that sent one.
    pub fn release(&mut self, physical: PhysicalKey) -> Option<u32> {
        self.held.remove(&physical)
    }

    /// Release every key held, as a window losing the focus must: the keys
    /// come up where the Mac cannot see them.
    pub fn release_all(&mut self) -> Vec<u32> {
        self.held.drain().map(|(_, keysym)| keysym).collect()
    }

    fn holds(&self, pair: (u32, u32)) -> bool {
        self.held.values().any(|&k| k == pair.0 || k == pair.1)
    }

    fn named(&self, key: NamedKey, location: KeyLocation) -> Option<u32> {
        let side = |pair: (u32, u32)| {
            Some(match location {
                KeyLocation::Right => pair.1,
                _ => pair.0,
            })
        };
        let (control, command) = if self.ctrl_as_command {
            (SUPER, CONTROL)
        } else {
            (CONTROL, SUPER)
        };
        let keypad = location == KeyLocation::Numpad;
        Some(match key {
            NamedKey::Shift => return side(SHIFT),
            NamedKey::Control => return side(control),
            NamedKey::Alt => return side(META),
            NamedKey::Super | NamedKey::Meta | NamedKey::Hyper => return side(command),
            NamedKey::Enter if keypad => 0xff8d,
            NamedKey::Enter => 0xff0d,
            NamedKey::Tab => 0xff09,
            NamedKey::Space => 0x20,
            NamedKey::Backspace => 0xff08,
            NamedKey::Escape => 0xff1b,
            NamedKey::Delete => 0xffff,
            NamedKey::Home => 0xff50,
            NamedKey::ArrowLeft => 0xff51,
            NamedKey::ArrowUp => 0xff52,
            NamedKey::ArrowRight => 0xff53,
            NamedKey::ArrowDown => 0xff54,
            NamedKey::PageUp => 0xff55,
            NamedKey::PageDown => 0xff56,
            NamedKey::End => 0xff57,
            NamedKey::Help => 0xff6a,
            NamedKey::F1 => 0xffbe,
            NamedKey::F2 => 0xffbf,
            NamedKey::F3 => 0xffc0,
            NamedKey::F4 => 0xffc1,
            NamedKey::F5 => 0xffc2,
            NamedKey::F6 => 0xffc3,
            NamedKey::F7 => 0xffc4,
            NamedKey::F8 => 0xffc5,
            NamedKey::F9 => 0xffc6,
            NamedKey::F10 => 0xffc7,
            NamedKey::F11 => 0xffc8,
            NamedKey::F12 => 0xffc9,
            NamedKey::F13 => 0xffca,
            NamedKey::F14 => 0xffcb,
            NamedKey::F15 => 0xffcc,
            NamedKey::F16 => 0xffcd,
            NamedKey::F17 => 0xffce,
            NamedKey::F18 => 0xffcf,
            NamedKey::F19 => 0xffd0,
            NamedKey::F20 => 0xffd1,
            _ => return None,
        })
    }

    /// The character a key types on the Mac.
    fn character(&self, key: &KeyInput) -> Option<char> {
        if self.holds(CONTROL) || self.holds(SUPER) {
            // A shortcut: the letter on the key, in the case Shift gives it.
            let letter = key.base.and_then(single).filter(|c| c.is_alphabetic());
            let c = letter.or_else(|| single(key.logical))?;
            if !c.is_alphabetic() {
                return Some(c);
            }
            let cased: Vec<char> = if self.holds(SHIFT) {
                c.to_uppercase().collect()
            } else {
                c.to_lowercase().collect()
            };
            return Some(if let [one] = cased[..] { one } else { c });
        }
        // Typing: what the layout made, a dead key's accent included.
        let typed = key.text.and_then(|text| {
            let mut chars = text.chars();
            chars.next().filter(|c| chars.next().is_none() && !c.is_control())
        });
        typed.or_else(|| single(key.logical))
    }
}

/// The one character a key stands for.
fn single(key: &Key) -> Option<char> {
    let Key::Character(text) = key else {
        return None;
    };
    let mut chars = text.chars();
    chars.next().filter(|c| chars.next().is_none() && !c.is_control())
}

fn character_keysym(c: char, location: KeyLocation) -> Option<u32> {
    if location == KeyLocation::Numpad {
        let keypad = match c {
            '0'..='9' => Some(0xffb0 + (c as u32 - '0' as u32)),
            '*' => Some(0xffaa),
            '+' => Some(0xffab),
            '-' => Some(0xffad),
            '.' | ',' => Some(0xffae),
            '/' => Some(0xffaf),
            '=' => Some(0xffbd),
            _ => None,
        };
        if keypad.is_some() {
            return keypad;
        }
    }
    let code = c as u32;
    match code {
        0x20..=0x7e => Some(code),
        _ if c.is_control() => None,
        _ => Some(UNICODE | code),
    }
}

#[cfg(test)]
mod tests {
    use winit::keyboard::{KeyCode, SmolStr};

    use super::*;

    fn character(text: &str) -> Key {
        Key::Character(SmolStr::new(text))
    }

    /// What a first press sends.
    fn down(press: Option<Press>) -> Option<u32> {
        match press {
            Some(Press::Down(keysym)) => Some(keysym),
            None => None,
            Some(repeat) => panic!("{repeat:?}"),
        }
    }

    /// Press a key by its code, with what the layout made of it.
    fn press(
        keyboard: &mut Keyboard,
        code: KeyCode,
        logical: Key,
        base: Option<Key>,
        text: Option<&str>,
    ) -> Option<u32> {
        down(keyboard.press(&KeyInput {
            physical: PhysicalKey::Code(code),
            logical: &logical,
            base: base.as_ref(),
            text,
            location: KeyLocation::Standard,
        }))
    }

    fn modifier(keyboard: &mut Keyboard, code: KeyCode, named: NamedKey, location: KeyLocation) -> Option<u32> {
        down(keyboard.press(&KeyInput {
            physical: PhysicalKey::Code(code),
            logical: &Key::Named(named),
            base: None,
            text: None,
            location,
        }))
    }

    #[test]
    fn typing_sends_the_character_the_layout_made() {
        let mut keyboard = Keyboard::default();
        let a = press(
            &mut keyboard,
            KeyCode::KeyA,
            character("A"),
            Some(character("a")),
            Some("A"),
        );
        assert_eq!(a, Some(0x41));
        // A German layout's Shift-7 and AltGr-Q.
        let slash = press(
            &mut keyboard,
            KeyCode::Digit7,
            character("/"),
            Some(character("7")),
            Some("/"),
        );
        assert_eq!(slash, Some(0x2f));
        let at = press(
            &mut keyboard,
            KeyCode::KeyQ,
            character("@"),
            Some(character("q")),
            Some("@"),
        );
        assert_eq!(at, Some(0x40));
        let umlaut = press(&mut keyboard, KeyCode::Semicolon, character("ö"), None, Some("ö"));
        assert_eq!(umlaut, Some(0x0100_00f6));
        let euro = press(&mut keyboard, KeyCode::KeyE, character("€"), None, Some("€"));
        assert_eq!(euro, Some(0x0100_20ac));
    }

    #[test]
    fn a_dead_key_sends_nothing_and_the_next_key_its_composition() {
        let mut keyboard = Keyboard::default();
        let dead = press(&mut keyboard, KeyCode::Equal, Key::Dead(Some('´')), None, None);
        assert_eq!(dead, None);
        let e = press(
            &mut keyboard,
            KeyCode::KeyE,
            character("e"),
            Some(character("e")),
            Some("é"),
        );
        assert_eq!(e, Some(0x0100_00e9));
    }

    #[test]
    fn a_repeat_and_a_release_send_what_the_press_sent() {
        let mut keyboard = Keyboard::default();
        press(&mut keyboard, KeyCode::KeyA, character("a"), None, Some("a"));
        // Shift comes down while A is held: the layout now says "A".
        modifier(&mut keyboard, KeyCode::ShiftLeft, NamedKey::Shift, KeyLocation::Left);
        let repeat = keyboard.press(&KeyInput {
            physical: PhysicalKey::Code(KeyCode::KeyA),
            logical: &character("A"),
            base: None,
            text: Some("A"),
            location: KeyLocation::Standard,
        });
        assert_eq!(repeat, Some(Press::Repeat(0x61)));
        assert_eq!(keyboard.release(PhysicalKey::Code(KeyCode::KeyA)), Some(0x61));
        assert_eq!(keyboard.release(PhysicalKey::Code(KeyCode::KeyA)), None);
    }

    #[test]
    fn a_held_modifier_does_not_repeat() {
        let mut keyboard = Keyboard::default();
        let shift = KeyInput {
            physical: PhysicalKey::Code(KeyCode::ShiftLeft),
            logical: &Key::Named(NamedKey::Shift),
            base: None,
            text: None,
            location: KeyLocation::Left,
        };
        assert_eq!(keyboard.press(&shift), Some(Press::Down(0xffe1)));
        assert_eq!(keyboard.press(&shift), None);
        assert_eq!(keyboard.release(shift.physical), Some(0xffe1));
    }

    #[test]
    fn modifiers_go_out_as_the_mac_reads_them() {
        let mut keyboard = Keyboard::default();
        let cases = [
            (KeyCode::ShiftRight, NamedKey::Shift, KeyLocation::Right, 0xffe2),
            (KeyCode::ControlLeft, NamedKey::Control, KeyLocation::Left, 0xffe3),
            (KeyCode::AltLeft, NamedKey::Alt, KeyLocation::Left, 0xffe7),
            (KeyCode::AltRight, NamedKey::Alt, KeyLocation::Right, 0xffe8),
            (KeyCode::SuperLeft, NamedKey::Super, KeyLocation::Left, 0xffeb),
        ];
        for (code, named, location, keysym) in cases {
            assert_eq!(
                modifier(&mut keyboard, code, named, location),
                Some(keysym),
                "{named:?}"
            );
        }
        let mut swapped = Keyboard::new(true);
        let control = modifier(&mut swapped, KeyCode::ControlLeft, NamedKey::Control, KeyLocation::Left);
        assert_eq!(control, Some(0xffeb));
        let windows = modifier(&mut swapped, KeyCode::SuperLeft, NamedKey::Super, KeyLocation::Left);
        assert_eq!(windows, Some(0xffe3));
    }

    #[test]
    fn keys_the_mac_lacks_are_not_sent() {
        let mut keyboard = Keyboard::default();
        for (code, named) in [
            (KeyCode::CapsLock, NamedKey::CapsLock),
            (KeyCode::NumLock, NamedKey::NumLock),
            (KeyCode::Insert, NamedKey::Insert),
            (KeyCode::Pause, NamedKey::Pause),
            (KeyCode::PrintScreen, NamedKey::PrintScreen),
            (KeyCode::ContextMenu, NamedKey::ContextMenu),
            (KeyCode::AltRight, NamedKey::AltGraph),
        ] {
            assert_eq!(
                modifier(&mut keyboard, code, named, KeyLocation::Standard),
                None,
                "{named:?}"
            );
        }
        assert!(keyboard.release_all().is_empty());
    }

    #[test]
    fn a_shortcut_takes_the_letter_in_the_case_shift_gives_it() {
        let mut keyboard = Keyboard::default();
        modifier(&mut keyboard, KeyCode::SuperLeft, NamedKey::Super, KeyLocation::Left);
        // Caps Lock on: the layout says "Z", Shift is not held.
        let z = press(&mut keyboard, KeyCode::KeyZ, character("Z"), Some(character("z")), None);
        assert_eq!(z, Some(0x7a));
        keyboard.release(PhysicalKey::Code(KeyCode::KeyZ));
        // Caps Lock and Shift: the layout says "z", the shortcut is Shift-Z.
        modifier(&mut keyboard, KeyCode::ShiftLeft, NamedKey::Shift, KeyLocation::Left);
        let shifted = press(&mut keyboard, KeyCode::KeyZ, character("z"), Some(character("z")), None);
        assert_eq!(shifted, Some(0x5a));
        // A symbol keeps what the layout made of it.
        let question = press(
            &mut keyboard,
            KeyCode::Slash,
            character("?"),
            Some(character("/")),
            None,
        );
        assert_eq!(question, Some(0x3f));
    }

    #[test]
    fn control_sends_the_letter_not_a_control_character() {
        let mut keyboard = Keyboard::default();
        modifier(
            &mut keyboard,
            KeyCode::ControlLeft,
            NamedKey::Control,
            KeyLocation::Left,
        );
        let c = press(
            &mut keyboard,
            KeyCode::KeyC,
            character("c"),
            Some(character("c")),
            Some("\u{3}"),
        );
        assert_eq!(c, Some(0x63));
    }

    #[test]
    fn the_keypad_sends_keypad_keysyms() {
        let mut keyboard = Keyboard::default();
        let mut keypad = |code, logical: Key| {
            down(keyboard.press(&KeyInput {
                physical: PhysicalKey::Code(code),
                logical: &logical,
                base: None,
                text: None,
                location: KeyLocation::Numpad,
            }))
        };
        assert_eq!(keypad(KeyCode::Numpad7, character("7")), Some(0xffb7));
        assert_eq!(keypad(KeyCode::NumpadDecimal, character(",")), Some(0xffae));
        assert_eq!(keypad(KeyCode::NumpadEnter, Key::Named(NamedKey::Enter)), Some(0xff8d));
        // Num Lock off: the key is an arrow.
        assert_eq!(keypad(KeyCode::Numpad8, Key::Named(NamedKey::ArrowUp)), Some(0xff52));
    }

    #[test]
    fn losing_the_focus_releases_every_key_held() {
        let mut keyboard = Keyboard::default();
        modifier(&mut keyboard, KeyCode::AltLeft, NamedKey::Alt, KeyLocation::Left);
        press(&mut keyboard, KeyCode::Tab, Key::Named(NamedKey::Tab), None, None);
        let mut released = keyboard.release_all();
        released.sort();
        assert_eq!(released, [0xff09, 0xffe7]);
        assert!(keyboard.release_all().is_empty());
    }
}
