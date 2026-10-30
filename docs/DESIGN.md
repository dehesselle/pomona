# Design

This document describes how Pomona is built and why: its parts, its threads, where code
from other projects lives, and the rules the code must keep.

## Goal

Pomona shows and controls a Mac's desktop over Apple Screen Sharing's own protocol, so
that it feels like Apple's viewer in Standard mode, which is far faster than a generic
VNC viewer. It is a library first and a command-line tool second. qem is to use it
for its desktop command.

- **TCP only.** The Mac is reached through one TCP connection, usually a local port
  forwarded over ssh. That rules out High Performance mode, whose picture comes over UDP
  ([high-performance.md](high-performance.md)).
- **Adaptive quality.** It carried 45 updates a second in a tenth of the bandwidth of
  zlib or ZRLE ([measurements.md](measurements.md)).
- **A native window, no browser,** on Windows and macOS first. Linux may follow.

## Parts

```text
src/pomona/                   Python
  session.py   connect(), Session: the threads, arming, input
  connection.py  version, type-30 sign-in, ServerInit, prelude, rekey
  record.py    the record layer
  stream.py    whole server messages out of the decrypted stream
  layout.py    the display layout
  cursor.py    the pointer's shapes
  messages.py  everything sent to the Mac, as bytes
  window.py    run_window(): a session in a window
  cli.py       the `pomona` command
crates/pomona-native/         Rust: pomona._native
  canvas.rs    the framebuffer every codec draws on
  inflate.rs   a deflate stream that lasts the session
  zrle.rs      ZRLE's tiles
  picture.rs   Picture, the canvas as Python sees it, and run_window
  window.rs    the window: winit's event loop, wake-ups, the cursor
  gpu.rs       wgpu: the texture, uploads, drawing
crates/apple-adaptive/        Rust: zDesk's Adaptive decoder, unchanged
```

The protocol is in Python, where it is easy to follow and to change. Decoding is in Rust,
because the Adaptive decoder in pure Python ran at a tenth of real time and natively at
seventeen times it ([measurements.md](measurements.md)). Python's `cryptography` does the
AES; at Adaptive's few Mbit/s the record layer in Python costs nothing worth moving.

## Threads

A session has two threads of its own, and the caller's.

- **The reader** takes bytes off the socket, opens records, parses messages and decodes
  rectangles onto the picture. It decodes with the GIL released, and waits on nothing
  else. When the session ends, it records why in `Session.error`.
- **The writer** sends whatever is queued for the Mac, in order, through the record
  layer's sending side. Everything the session sends goes through its queue, so the
  sequence numbers stay in step.
- **The caller** sends input by queueing it, which never blocks. With a window, the
  caller is the window's event loop, on the thread that called `run_window`.

This split follows from the Mac: it reads nothing while it writes an update, and at push
interval 0 it writes updates for as long as the screen changes. A viewer that stops
reading to decode or paint stalls its own input, and one that writes from the UI thread
can block it ([protocol.md](protocol.md#what-arming-does)).

## The picture

`Picture` holds one RGBA framebuffer, the canvas, behind a mutex. Every codec draws on
it, so whatever the Mac sends, there is one buffer to show. The Adaptive decoder keeps a
framebuffer of its own, because its tiles copy from and refine what earlier rectangles
left there; each rectangle it draws is copied onto the canvas. zlib and ZRLE each keep
one deflate stream for the session. A rectangle that fails to inflate loses its stream,
and `Picture.decode` raises `StreamError` for it, a `ValueError` the session treats as
the end; any other failure is a plain `ValueError` the session survives.

The pointer is not part of the picture: the Mac sends its shapes separately, and the
window is to show them as the system cursor. Shapes are few and small, so `cursor.py`
decodes them in Python.

The canvas is a mutex, not Python's borrow checking, so that the reader can decode
with the GIL released while another thread reads the picture.

## The window

The window lives in the Rust extension: **winit** for the window and its events,
**wgpu** (D3D12 and Metal only) to show the canvas as a texture. The reasons:

- Decoded pixels never pass through Python: the window uploads the canvas's changed
  rectangles straight into the texture.
- The GPU scales for free, which a window smaller or denser than the framebuffer needs.
- No large framework: Qt was ruled out; wgpu-py with glfw would work too, but would hand
  every frame through Python.

How it runs:

- `run_window` runs winit's event loop on the calling thread with the GIL released,
  and takes the GIL only to tell Python about the window (`on_event`). macOS requires
  the main thread; Windows does not, so the loop is allowed on any thread there. winit
  allows one event loop per process, so a process opens one window in its life.
- The canvas records which rectangles changed. Decoding wakes the loop through the
  picture's waker; wake-ups coalesce, one outstanding at a time, and the loop uploads
  everything that changed since the last upload. A burst of strips from the Mac
  therefore costs one frame, not one per strip.
- The picture is drawn by one triangle sampling the texture, as large as fits, centred
  and on whole pixels, over a dark grey letterbox. Linear filtering is exact at one to
  one and smooth when scaled.
- With no GPU, wgpu takes the system's software adapter (WARP on Windows), which is
  enough for a framebuffer this size.
- wgpu reports errors to a handler that would panic by default. Pomona's handler keeps
  the first error, and the window ends with a `RuntimeError` instead.
- The Mac's pointer shapes become the window's cursor.
- The session closes the window when it ends, and closing the window ends the session.
- When the window says its scale (pixels per point), the session asks a Retina Mac for
  `min(1, client scale / Mac density)` with `SetServerScaling`, as Apple's viewer does,
  so the Mac sends its screen at the client's density.

Input is next: winit's events go out as X11 keysyms and positions in framebuffer pixels;
the session applies the Mac's rules. Keyboard grab on Windows is to be a low-level
keyboard hook; the clipboard `arboard`.

## Build and version

One maturin distribution holds the Python package and the extension. maturin cannot take
a version from git tags, so the version lives in `Cargo.toml`
(`workspace.package.version`) and is bumped by hand; `pomona --version` reads it from
the installed metadata.

## Code from other projects

Code taken from another project lives in a package of its own, so its origin and copyright
stay apart from Pomona's own code and the project stays REUSE compliant:

| Source | What Pomona uses | Where |
| --- | --- | --- |
| [zDesk](https://github.com/andrewtheguy/zdesk) (MIT) | `decoder.rs`, the Adaptive decoder, byte for byte | `crates/apple-adaptive` |
| zDesk's `docs/apple-vnc-889.md` | the protocol facts Pomona's own code is written from | [protocol.md](protocol.md) |
| [iShareScreen](https://github.com/renegadelink/iShareScreen) (AGPL-3.0-or-later) | nothing so far | a subpackage `src/pomona/iss/`, if ever |

The sign-in and the record layer are written from zDesk's description rather than taken
from iShareScreen, which has no type-30 sign-in and treats records as messages. Taking
iShareScreen code would make the distribution AGPL in part. What it has that Pomona may
want, at `9ab40d3a` (its package `isharescreen`):

- `proxy/protocol/auth.py`: SRP sign-in (type 36, and 33's SRP form), proven against a
  NoMac Mac;
- `proxy/protocol/negotiation.py`: the session-select exchange;
- `proxy/protocol/clipboard.py`, `proxy/local_clipboard.py`: the pasteboard archive;
- `frontend/desktop/keyboard_grab.py`: grabbing the keyboard on Windows (a
  `WH_KEYBOARD_LL` hook) and X11, and `keymap.py`.

Its builder for `AutoFrameBufferUpdate` sends the value that turns pushes off, and its
`parse_apple_display_layout` predates the corrected layout length; neither is worth
taking.

## Core principles

1. **The reader never waits on the caller, the UI or the writer,** because the Mac stops
   reading input while the viewer stops reading its updates.
2. **Everything sent after the rekey goes through the writer's queue,** because the
   record layer's sequence and CBC chain must see messages in sending order.
3. **Nothing goes in cleartext but the prelude,** and the prelude goes back to back,
   because the Mac switches to records the moment it sends the rekey.
4. **Records are not messages.** Bodies are concatenated and parsed as a stream, because
   the Mac cuts records wherever they are full.
5. **The session is armed again after every display layout,** because a layout drops the
   arming and the picture would stop.
6. **Every encoding listed can be read or stepped over,** because the Mac takes the list
   as a promise, and an unreadable rectangle loses the stream's framing.
7. **A second rekey ends the session,** because records already framed under the old
   key would fail the Mac's check.
8. **The library never prints, prompts or exits,** and never writes the password
   anywhere; only the CLI talks to the user. Errors are exceptions, or
   `Session.error` once the session runs.
9. **Pomona opens no listening sockets.**
10. **Code from other projects stays in its own package, unchanged where possible,** so
    an update is a copy and its origin stays visible.
11. **The window's event loop holds the GIL only while it calls into Python,** because
    the reader thread needs the GIL to go on parsing; a loop that kept it would stall
    the Mac's input as surely as a reader that stopped reading.
12. **Errors in the extension come out as exceptions, never panics,** wgpu's included,
    because a panic in the window would tear through the caller's Python.
