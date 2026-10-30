# Roadmap

This document lists what is still to be built, in order, with the questions each step
has to answer. A step leaves this list when it is done; what was built is described in
the other documents.

Every step ends with its tests passing on a devbox, and live against a NoMac Mac where
the Mac is involved.

## 1. The window on macOS, and on Retina

- Build and try the window on macOS. No devbox runs macOS; a NoMac Mac is the likely
  place, showing another NoMac Mac or itself.
- The keys on a macOS client: Command and Option, and Command-Q reaching the Mac with
  winit's menu off. They compile for macOS but have not run there.
- Check `SetServerScaling` live against a Retina Mac (NoMac's Macs are 1x), and the
  pointer's shape on a HiDPI client, where it is drawn at its size in points and so
  looks small.
- Measure what the window can draw, apart from the path: a paced replay of a capture.

Question: how does Adaptive look while it moves and refines (text while scrolling)?

## 2. Pasteboard and polish

- The pasteboard both ways, text only, through `arboard`
  ([protocol.md](protocol.md#the-pasteboard)).
- Reconnecting after the Mac drops the session.
- Acting on `MiscStatus` 17 (a login or logout at the Mac).
- Keys from a layout with AltGr and dead keys, live: so far only the unit tests and a
  US layout have tried them.
- The keyboard source the Mac reports (`0x455`): its layout, and whether a secure text
  field has the focus.

## 3. qem

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
