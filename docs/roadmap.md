# Roadmap

This document lists what is still to be built, in order, with the questions each step
has to answer. A step leaves this list when it is done; what was built is described in
the other documents.

Every step ends with its tests passing on a devbox, and live against a NoMac Mac where
the Mac is involved.

## 1. Input

- Keys from winit as X11 keysyms, with the Mac's rules: lowercase under Caps Lock, Alt
  sent as Meta (Option), the Windows key as Super (Command), Ctrl as Command optional.
  On a macOS client, Command as Super and Option as Meta, with winit's default menu off
  so Command-Q reaches the Mac ([protocol.md](protocol.md#input)).
- Mouse buttons, the scroll message, and position mapping through the window's scaling.
- Grabbing the keyboard on Windows while focused (the Windows key, Alt-Tab).
- Live: typing, clicking and scrolling in TextEdit.

Question: does the keyboard grab work with winit's event loop?

## 2. The window on macOS, and on Retina

- Build and try the window on macOS. No devbox runs macOS; a NoMac Mac is the likely
  place, showing another NoMac Mac or itself.
- Check `SetServerScaling` live against a Retina Mac (NoMac's Macs are 1x), and the
  pointer's shape on a HiDPI client, where it is drawn at its size in points and so
  looks small.
- Measure what the window can draw, apart from the path: a paced replay of a capture.

Question: how does Adaptive look while it moves and refines (text while scrolling)?

## 3. Pasteboard and polish

- The pasteboard both ways, text only, through `arboard`
  ([protocol.md](protocol.md#the-pasteboard)).
- Reconnecting after the Mac drops the session.
- Acting on `MiscStatus` 17 (a login or logout at the Mac).

## 4. qem

`qem desktop` uses Pomona in-process when installed (an extra, `qem[desktop]`):
qem opens the ssh forward and fetches the Mac's login, and hands both to Pomona, so
the password stays in memory. TigerVNC remains its fallback. This happens in qem's
repository, with its documentation. qem still needs a way to fetch the login and to
forward the Screen Sharing port for it.

## Later

- SRP sign-in ([protocol.md](protocol.md#other-sign-in-types)).
- Several displays: `SetDisplay`, and a choice of screen.
- Linux.
- The High Performance relay, if Adaptive turns out not to be enough
  ([high-performance.md](high-performance.md)).
