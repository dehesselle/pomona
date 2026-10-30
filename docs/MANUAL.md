# Manual

This document describes how to use Pomona: the `pomona` command and the Python library.

## Reaching the Mac

Pomona connects to the Mac's Screen Sharing port, 5900, over one TCP connection. Through
ssh, forward a local port to it:

```sh
ssh -N -L 127.0.0.1:5901:127.0.0.1:5900 user@mac
```

The account must be allowed to sign in to Screen Sharing: on the Remote Management list
with at least Observe and Control. See [OPERATIONS.md](OPERATIONS.md#troubleshooting).

## The command

```text
pomona [--version] HOST[:PORT] -u USER [--probe SECONDS] [--quality adaptive|full]
       [--push-interval US] [-v]
```

| Option | |
| --- | --- |
| `HOST[:PORT]` | the Mac; port 5900 by default. IPv6 as `[::1]` or `[::1]:5901`. |
| `-u`, `--user` | the macOS account to sign in as |
| `--probe SECONDS` | instead of a window: receive for that long, then print what arrived |
| `--quality` | `adaptive` (the default): Apple's tiles, coarse first and refined after; `full`: lossless zlib, about ten times the traffic |
| `--push-interval US` | pace the Mac's updates, in microseconds; 0 (the default) is as fast as it captures |
| `-v`, `-vv` | log more, to stderr |
| `--version` | print the version |

The password comes from the environment variable `POMONA_PASSWORD`, or is asked for
without echo. Pomona never takes it from the command line, and does not read it from a
pipe on stdin.

Without `--probe`, Pomona opens a window showing the Mac's screen, scaled to fit the
window and keeping its aspect. Closing the window ends the session, and the session
ending closes the window. With `-v` it logs, at the end, the updates received and the
frames drawn. Keyboard and mouse do not reach the Mac yet.

`--probe` reports the rate, the bandwidth, when the first picture came, and the
rectangles per encoding:

```text
signed in: 1440x900 'NoMac'
17.2 s: 1.88 Mbit/s, 393 updates (22.9/s)
first picture after 3.14 s
  encoding  1011:   387 rects,   4001928 bytes, 95.9 screens
  ...
updates per second: [0, 0, 3, 52, 17, ...]
```

It exits 0 when the session ran to the end or the window was closed, 1 when it failed,
the sign-in was refused or no window could be opened, and 2 for a usage error.

## The library

```python
from pomona import connect

with connect("127.0.0.1", 5901, user="me", password=password) as session:
    session.wait(10)
    pixels = session.picture.snapshot()  # RGBA, row after row
```

### `connect`

```python
connect(host, port=5900, *, user, password, quality="adaptive",
        push_interval_us=0, timeout=15.0, on_update=None) -> Session
```

Signs in and starts a Standard session. `quality` is `"adaptive"` or `"full"`. `timeout`
covers the connection and the handshake. It raises:

- `AuthenticationError` when the Mac refuses the sign-in, which is a wrong user name or
  password, or an account without Remote Management permission: the Mac does not say
  which.
- `ProtocolError` when the server does not speak as a Mac.
- `OSError` for the connection itself, including timeouts.

All of Pomona's own errors derive from `PomonaError`.

### `Session`

| Member | |
| --- | --- |
| `picture` | the framebuffer: `width`, `height`, `snapshot()` |
| `server` | the ServerInit: `width`, `height`, `name`, `flags`, `accepts(message_type)` |
| `layout` | the Mac's last display layout, or None before the first |
| `cursor` | the pointer's shape: `width`, `height`, `hotspot`, `rgba`; None until the Mac sends one |
| `cursor_position` | where the Mac last put the pointer, or None |
| `bytes_received` | bytes read off the connection so far |
| `on_update` | called with each `stream.FramebufferUpdate` once it is drawn |
| `closed`, `wait(timeout)` | whether the session has ended; wait for it to end |
| `error` | why it ended, if not by `close()` |
| `close()` | end it, after sending the input already queued |
| `key(keysym, down)` | press or release the key of an X11 keysym |
| `pointer(x, y, buttons)` | move the pointer with `LEFT`, `MIDDLE`, `RIGHT` held |
| `scroll(dx, dy, x, y)` | scroll by points, positive right and down |

- `on_update` runs on the session's reader thread. Keep it short: while it runs, nothing
  is read from the Mac, and the Mac then stops reading input.
- Positions are framebuffer pixels. The session converts them for a Mac that scales
  its framebuffer, and swaps right and middle buttons the way the Mac expects.
- Input never blocks; anything sent after the session ended is dropped.
- Pomona logs through the `logging` module under the `pomona` logger, and prints
  nothing.

### `run_window`

```python
from pomona import connect, run_window

session = connect("127.0.0.1", 5901, user="me", password=password)
run_window(session, title="My Mac")  # returns when the window closes
```

`run_window(session, *, title=None)` shows the session's screen in a window until the
window is closed or the session ends, and then closes the session.

- It runs on the calling thread, which on macOS must be the main thread. The session's
  own threads go on meanwhile; `on_update` still runs on the reader thread.
- A process can open one window in its life, a limit of winit's event loop.
- It raises `RuntimeError` when no window or graphics adapter can be had. A machine
  without a GPU uses the system's software renderer.
- `session.set_client_scale(scale)`, which the window calls with its pixels per point,
  makes a Retina Mac send its screen at that density.
- `session.picture.frames` counts the frames the window has drawn.
