# Roadmap

This document lists what is still to be built, in order, with the questions each step
has to answer. A step leaves this list when it is done; what was built is described in
the other documents.

Every step ends with its tests passing on a devbox, and live against a NoMac Mac where
the Mac is involved.

## 1. What the window can draw

- Measure what the window can draw, apart from the path: a paced replay of a capture.

Question: how does Adaptive look while it moves and refines (text while scrolling)?

## 2. qem

`qem desktop` uses Pomona in-process when installed (an extra, `qem[desktop]`):
qem opens the ssh forward and fetches the Mac's login, and hands both to Pomona, so
the password stays in memory. TigerVNC remains its fallback. This happens in qem's
repository, with its documentation. qem still needs a way to fetch the login and to
forward the Screen Sharing port for it.

## Later

- SRP sign-in ([protocol.md](protocol.md#other-sign-in-types)).
- Several displays: `SetDisplay`, and a choice of screen.
- Option on a macOS client, live. Command, and Command-Q reaching the Mac, were tried.
- Linux on a real desktop, by hand: so far the window ran on Xvfb with openbox (drawing
  live, input, the pasteboard both ways, keeping its shape) and on headless sway
  (drawing, keys, pointer moves). Untried: clicks and the wheel on Wayland, Shift on a
  real layout there, the pasteboard on Wayland, and a GPU.
- The High Performance relay, if Adaptive turns out not to be enough
  ([high-performance.md](high-performance.md)).
- Dead keys, live. Umlauts and AltGr on a German layout were tried by hand; dead keys
  only in the unit tests.
- A login or logout at the Mac, live: Pomona logs it and follows the session if the Mac
  closes it, but no live test has done one.
- Showing in the window when the Mac's focus is in a password field
  (`Session.keyboard_source`), and while the window is reconnecting.
