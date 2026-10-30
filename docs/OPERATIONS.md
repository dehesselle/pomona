# Operations

This document describes how a Pomona session behaves while it runs: its traffic, its
pacing, how it ends, and what to do when it fails.

## Traffic

- **One TCP connection** to the Mac's port 5900, and no listening sockets.
- **Adaptive quality** carried 2.5 Mbit/s for a constantly scrolling Terminal at 1440×900,
  and nothing while the screen is still. zlib and ZRLE carried ten times as much
  ([measurements.md](measurements.md)).
- **On a slow link** the Mac sends its coarse picture first and holds the refinement
  until the motion stops, so text sharpens a moment after scrolling ends.
- **Signing in** takes about half a second of CPU for the key exchange, plus a few round
  trips.

## Pacing

The Mac pushes every change unasked, as fast as it captures it (push interval 0, what
Apple's viewer asks for), and holds updates while its socket is backed up. The session's
reader drains the socket all the time, so the pace is set by the link.

`--push-interval` (or `push_interval_us`) caps the rate: 33,333 is about 30 updates a
second. Use it when the client cannot keep up. At interval 0, a client slower than the
Mac's capture has its keys and clicks wait until the screen stops changing, because the
Mac reads nothing while it writes ([protocol.md](protocol.md#what-arming-does)). The cost
of an interval is that a moving window arrives in strips captured at different moments.

## The window

- **Frames follow changes.** A still screen draws nothing. A change wakes the window,
  which uploads every rectangle changed since the last frame and draws once, so it
  draws fewer frames than the Mac sends updates: a moving window comes as strips
  milliseconds apart, and one frame takes them all. Drawing never holds up reading.
- **Scaling.** The picture is shown as large as the window allows, keeping its aspect,
  at one to one when the window is the framebuffer's size; the window opens that size
  when the screen has room, smaller when not.
- **Without a GPU**, wgpu draws with the system's software renderer (WARP on Windows).
  On a Windows VM at 1024×768 it showed a 1440×900 Mac scaled to fit, live
  ([measurements.md](measurements.md#pomona-live)).

## How a session ends

- **`close()`** sends what was already queued, then hangs up. `error` stays None.
  Closing the window does the same.
- **The Mac hangs up**, for example when its user ends the session: `error` says the Mac
  closed the connection.
- **The connection fails**: `error` holds the `OSError`.
- **The stream cannot be read**: an unknown message, an unknown encoding, a record that
  fails its integrity check, or a second rekey end the session with a `ProtocolError`,
  because nothing after them can be trusted.
- **A zlib or ZRLE rectangle that does not inflate** ends it with a `ProtocolError`:
  every rectangle is inflated on what the ones before it left, so the rest of the
  picture is lost with it.
- **Any other rectangle that does not decode** does not end it: the stream's framing is
  intact, the picture keeps what was drawn, and the next update paints over the gap. A
  pointer shape that does not decode leaves the old shape. Both are logged as warnings.

## Troubleshooting

**"the Mac refused the sign-in".** A wrong password, or an account that may not use
Remote Management: the Mac answers both the same way. Remote Management's default "All
users" refuses valid accounts. Add the account to the list with at least Observe and
Control, in System Settings → General → Sharing → Remote Management (ⓘ), or over ssh:

```sh
sudo /System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart \
  -configure -access -on -users <account> -privs -all -restart -agent
```

**"not a Mac: the server speaks ..."** The port is not a Mac's Screen Sharing, or the
forward points elsewhere.

**"the Mac offers no sign-in Pomona speaks".** The Mac's security types lack 30. A
Remote Management preference can reduce the list to one type.

**Signed in, but no picture.** The first picture follows the first display layout. If
none comes, check the Mac's own log, over ssh on the Mac (the full path, since zsh's
`log` builtin shadows it):

```sh
/usr/bin/log show --last 10m --style compact --predicate 'process CONTAINS[c] "screenshar"'
```

**Low update rates.** Compare over the same path only: every ssh hop adds latency, and
the Mac paces itself by how fast its socket drains ([measurements.md](measurements.md)).

**"no graphics adapter can draw to the window", or "the window cannot be drawn to".**
Pomona found no D3D12 (Windows) or Metal (macOS) device for the window. On Windows, a
process without a desktop cannot draw: an ssh session runs in session 0, which has none.
Run Pomona in a desktop session.
