# Manual

This document describes how to use Pomona: the `pomona` command and the Python library.

## Reaching the Mac

Pomona connects to the Mac's Screen Sharing port, 5900, over one TCP connection: to a
Mac on the network or the internet directly, `pomona mac.example.com -u me`, or through
ssh, forwarding a local port to it:

```sh
ssh -N -L 127.0.0.1:5901:127.0.0.1:5900 user@mac
```

The password goes encrypted under a key from a Diffie-Hellman exchange, and the session
is encrypted after a short cleartext prelude that carries nothing secret
([protocol.md](protocol.md#connecting-and-signing-in)). The exchange proves neither
side's identity, though: it keeps the password from anyone listening, not from someone
who can intercept and relay the connection. Over the open internet, ssh or a VPN closes
that gap.

The account must be allowed to sign in to Screen Sharing: on the Remote Management list
with at least Observe and Control. See [OPERATIONS.md](OPERATIONS.md#troubleshooting).

## The command

```text
pomona [--version] HOST[:PORT] -u USER [--probe SECONDS] [--quality adaptive|full]
       [--push-interval US] [--ctrl-as-command] [--no-pasteboard]
       [--scale FACTOR | --size WxH] [-v]
```

| Option | |
| --- | --- |
| `HOST[:PORT]` | the Mac; port 5900 by default. IPv6 as `[::1]` or `[::1]:5901`. |
| `-u`, `--user` | the macOS account to sign in as |
| `--probe SECONDS` | instead of a window: receive for that long, then print what arrived |
| `--quality` | `adaptive` (the default): Apple's tiles, coarse first and refined after; `full`: lossless zlib, about ten times the traffic |
| `--push-interval US` | pace the Mac's updates, in microseconds; 0 (the default) is as fast as it captures |
| `--ctrl-as-command` | send Control as Command and the Windows key as Control, so Ctrl-C copies on the Mac |
| `--no-pasteboard` | share no text between your pasteboard and the Mac's |
| `--scale FACTOR` | open the window at FACTOR (at most 1) times the Mac's screen in pixels |
| `--size WxH` | open the window as large as fits in W × H pixels |
| `-v`, `-vv` | log more, to stderr |
| `--version` | print the version |

The password comes from the environment variable `POMONA_PASSWORD`, or is asked for in
the terminal, without echo. Pomona never takes it from the command line, and does not
read it from a pipe on stdin. It keeps it in memory while the window is open, to sign
in again after a dropped connection.

Without `--probe`, Pomona opens a window showing the Mac's screen. Closing the window
ends the session. With `-v` it logs, at the end, the updates received and the frames
drawn.

The Mac sends its screen at the window's size, pixel for pixel: it scales the screen
down before sending, and again after you resize the window. Its own screen stays as it
is. The window:

- opens at `--scale` times the Mac's screen, or as large as fits in `--size`, or by
  default at the size that shows the Mac's text as large as your own; in any case at
  most nine tenths of your screen;
- keeps the Mac's shape while you resize it, so the picture fills it, and grows no
  larger than the Mac's screen (it has no maximize button);
- asks the Mac for its new size once you stop resizing, which costs one whole screen
  of traffic.

When the connection drops, the window stays and Pomona signs in again, after 1, 2, 5,
10, 20 and then every 30 seconds, logging each attempt, until it is back, the Mac
refuses the sign-in, or you close the window. When the Mac's user ends the session, the
window closes.

Text you copy on the Mac lands on your pasteboard. Text you copy here goes to the Mac
when the window gets the focus, so it is there to paste. Only text is shared, and
`--no-pasteboard` shares none.

Keys, clicks and the mouse wheel in the window go to the Mac:

- Keys type what your keyboard layout makes of them, accents and AltGr included, into
  whatever layout the Mac has.
- Alt is Option and the Windows key is Command, as on a Mac keyboard where they sit.
  With `--ctrl-as-command`, Control is Command and the Windows key Control instead.
- On Windows the window keeps the Windows keys, Alt-Tab, Alt-Esc, Alt-Space and
  Ctrl-Esc while it is in front, so they reach the Mac: Windows-Tab is Command-Tab.
  Ctrl-Alt-Del stays with Windows. On Linux the desktop keeps the shortcuts it takes,
  such as Super's and Alt-Tab.
- Caps Lock, Num Lock, Insert, Pause, Scroll Lock, Print and the menu key are not sent;
  the Mac has nothing for them.

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
the sign-in was refused, no window could be opened or no password was given (Ctrl-D at
an empty prompt, outside Windows), 2 for a usage error, and 130 when Ctrl-C stopped it.

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
        push_interval_us=0, timeout=15.0, on_update=None, on_pasteboard=None,
        picture=None) -> Session
```

Signs in and starts a Standard session. `quality` is `"adaptive"` or `"full"`. `timeout`
covers the connection and the handshake. `picture` draws the session on an existing
picture, such as a window's when signing in again. It raises:

- `AuthenticationError` when the Mac refuses the sign-in, which is a wrong user name or
  password, or an account without Remote Management permission: the Mac does not say
  which.
- `ProtocolError` when the server does not speak as a Mac.
- `OSError` for the connection itself, including timeouts.

All of Pomona's own errors derive from `PomonaError`. A session's `error` can also be
`EndedAtMacError`, when the Mac's user ended the session.

### `Session`

| Member | |
| --- | --- |
| `picture` | the framebuffer: `width`, `height`, `snapshot()` |
| `server` | the ServerInit: `width`, `height`, `name`, `flags`, `accepts(message_type)` |
| `layout` | the Mac's last display layout, or None before the first |
| `cursor` | the pointer's shape: `width`, `height`, `hotspot`, `rgba`; None until the Mac sends one |
| `cursor_position` | where the Mac last put the pointer, or None |
| `pasteboard` | the text last on the Mac's pasteboard as far as the session knows, or None |
| `keyboard_source` | the Mac's keyboard: `layout` (such as `com.apple.keylayout.US`) and `secure`, true while a password field has the focus; None until the Mac says |
| `bytes_received` | bytes read off the connection so far |
| `on_update` | called with each `stream.FramebufferUpdate` once it is drawn |
| `on_pasteboard` | called with the text of the Mac's pasteboard each time it changes |
| `set_pasteboard(text)` | put text on the Mac's pasteboard; empty text clears it |
| `native_size` | the Mac's screen in pixels before it scales it: the most it can send |
| `density` | the Mac's pixels per point, once a layout says |
| `request_scale(factor)` | have the Mac scale its screen by `factor`, in (0, 1], before sending it |
| `on_layout` | called with each display layout once it is applied |
| `closed`, `wait(timeout)` | whether the session has ended; wait for it to end and its reader to stop, after which nothing draws on the picture or calls `on_update` |
| `error` | why it ended, if not by `close()` |
| `close()` | end it, after sending the input already queued |
| `key(keysym, down)` | press or release the key of an X11 keysym, as the Mac reads it |
| `pointer(x, y, buttons)` | move the pointer with `LEFT`, `MIDDLE`, `RIGHT` held |
| `scroll(dx, dy, x, y)` | scroll by points, positive right and down |

- `on_update` and `on_pasteboard` run on the session's reader thread. Keep them short:
  while they run, nothing is read from the Mac, and the Mac then stops reading input.
- The Mac reports text you gave it with `set_pasteboard` back as a change of its own.
- Positions are framebuffer pixels. The session converts them for a Mac that scales
  its framebuffer, and swaps right and middle buttons the way the Mac expects.
- Keysyms go out as given, and the Mac reads them its own way
  ([protocol.md](protocol.md#input)): `Meta_L` (`0xffe7`) is Option, `Super_L`
  (`0xffeb`) and `Alt_L` both Command. Send characters beyond ASCII as Unicode keysyms
  (`0x01000000` plus the code point), and a key held down as repeated releases and
  presses: the Mac repeats nothing itself.
- Input never blocks; anything sent after the session ended is dropped.
- Pomona logs through the `logging` module under the `pomona` logger, and prints
  nothing.

### `run_window`

```python
from pomona import connect, run_window

session = connect("127.0.0.1", 5901, user="me", password=password)
run_window(session, title="My Mac")  # returns when the window closes
```

```python
run_window(session, *, title=None, ctrl_as_command=False, pasteboard=True,
           reconnect=None, scale=None, size=None) -> Session
```

Shows the session's screen in a window until the window is closed or the session ends,
and then closes the session. The window's size, keys, clicks, scrolling and the
pasteboard work as the command describes; `ctrl_as_command` is `--ctrl-as-command`,
`pasteboard=False` is `--no-pasteboard`, `scale` is `--scale`, and `size` is `--size` as
a `(width, height)` tuple.

With `reconnect`, a session whose connection dropped is followed by another:
`reconnect(picture)` signs in again and returns a session drawing on the window's
picture, as `connect(..., picture=picture)` does. It is retried as the command
describes. `run_window` returns the last session, which is `session` unless it was
followed:

```python
def sign_in(picture=None):
    return connect("mac.example.com", user="me", password=password, picture=picture)


session = run_window(sign_in(), reconnect=sign_in)
```

- It runs on the calling thread, which on macOS must be the main thread. The session's
  own threads go on meanwhile; `on_update` still runs on the reader thread.
- A process can open one window in its life, a limit of winit's event loop.
- It raises `RuntimeError` when no window or graphics adapter can be had. A machine
  without a GPU uses the system's software renderer.
- The window asks the Mac for its own size with `session.request_scale(factor)`.
  Without a window, `request_scale` asks for any factor in (0, 1], and
  `set_client_scale(scale)` for the one that sends a Retina Mac's screen at the
  client's density, as Apple's viewer does.
- `session.picture.frames` counts the frames the window has drawn.
