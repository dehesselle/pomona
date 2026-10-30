# Apple Screen Sharing's protocol, as Pomona speaks it

This document describes RFB 003.889, Apple's revision of VNC, as far as Pomona's Standard
mode depends on it: connecting, signing in, the record layer, the picture, input and the
pasteboard.

Apple documents none of it. Most of it comes from zDesk's
[`docs/apple-vnc-889.md`](https://github.com/andrewtheguy/zdesk) (MIT, read at
`2072baf3`), measured against macOS 26.5–26.6 Apple Virtualization guests. The rest was
measured by Pomona against NoMac Macs, which are the same kind of guest (macOS 26.6,
`VirtualMac2,1`, 1440×900); those statements are marked **[measured]**. Nothing here was
checked on a physical Mac, and a macOS update can change any of it.

All numbers on the wire are big-endian.

## Connecting and signing in

The viewer opens one TCP connection to port 5900. Pomona reaches it through an ssh
forward, so the Mac sees the connection coming from its own 127.0.0.1.

1. **Version.** The Mac sends `RFB 003.889\n`, and the viewer answers the same.
2. **Security types.** A `u8` count, then the types. A NoMac Mac offered `30 33 36 35`
   **[measured]**: 30 Diffie-Hellman, 33 RSA, 36 SRP and 35 Kerberos. Type 2, the plain
   VNC password, appears only when "VNC viewers may control screen with password" is on.
   A count of 0 is followed by a `u32` length and a reason.
3. **Type 30**, which Pomona uses:
   - The Mac sends a `u16` generator, a `u16` key length (Pomona accepts 64 to 1024),
     the prime and its public key. macOS 26 uses RFC 5054's 4096-bit group with
     generator 5, so both keys are 512 bytes.
   - The viewer draws a private exponent of twice the key length, as Apple's viewer
     does. The shared secret is left-padded to the key length before it is hashed:
     without the padding, a secret with a leading zero byte derives the wrong key.
   - The key is `MD5(shared secret)`. It encrypts a 128-byte credential block under
     AES-128 in **ECB** mode: the user name at offset 0 and the password at 64, each
     NUL-terminated, over random filler. The viewer then sends its public key.
   - The same MD5 key later unwraps the rekey.
4. **SecurityResult**: a `u32`, 0 for success. A refusal carries no reason on 003.889,
   and the Mac then leaves the connection open.
5. **ClientInit** `0x81`: share the screen (`0x01`), and send the extended ServerInit
   (`0x80`).
6. **ServerInit**: `u16` width and height, 16 bytes of the Mac's pixel format, and a
   `u32`-counted "name". In the extended form the name is 22 bytes of structure first:
   a zero `u16`, a `u32` of flags and a 16-byte bitmap of the client message types the
   Mac accepts (type `t` is bit `7 − t % 8` of byte `t / 8`). The real name follows as
   UTF-8. A NoMac Mac calls itself `NoMac` **[measured]**.

   | Flag | Meaning |
   | --- | --- |
   | `0x01` | observe only |
   | `0x02` | the user may control |
   | `0x04` | a session-select block follows (only when ClientInit set `0x40`) |
   | `0x08` | screen capture not permitted |
   | `0x10` | extended ServerInit |
   | bits 5+ | the most virtual displays the Mac creates |

**A refused sign-in looks the same whatever its cause.** An account without Remote
Management permission is refused exactly like a wrong password. The default "All users"
setting refuses valid accounts; the account has to be on the list, with at least Observe
and Control. Over ssh on the Mac:

```sh
sudo /System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart \
  -configure -access -on -users <account> -privs -all -restart -agent
```

NoMac's `agent` account is set up this way.

**The session-select exchange** happens only when someone other than the signing-in
user is logged in at the Mac and the viewer set `0x40`. Pomona never sets it.

**No warm-up connection is needed.** iShareScreen opens a throwaway connection first;
Pomona's live runs signed in without one **[measured]**.

## The cleartext prelude and the rekey

Right after ServerInit the viewer sends, back to back and in cleartext:

1. `ViewerInfo` (`0x21`), 66 bytes: a pad, a `u16` 62, a `u16` application class (1),
   a `u32` application id (2, Screen Sharing), the application version 6.1.0 and the
   OS version 26.6.2 as `u32` triples, and a 32-byte bitmap of the server messages the
   viewer handles. A body of another size makes the Mac swallow the next message.
2. `SetMode` (`0a 00 0001`): control. 0 would be observe, 2 control with the Mac's own
   keyboard and mouse locked.
3. `AutoPasteboard` (`15 00 0001 00000000`): report pasteboard changes. It only works
   in cleartext, and only after the two messages before it.
4. `SetEncryption` command 1 (`1200 0001 0001 0001 00000001`): start the record layer
   with method 1. The Mac answers with the rekey.
5. `SetEncryption` command 2 (`1200 0002 0001 0000`): decrypt what the viewer sends from
   now on.

Back to back matters: the Mac switches to records the moment it sends the rekey, so
nothing may wait for an answer in between.

The viewer then reads cleartext until the rekey: a framebuffer update of one rectangle
with encoding `0x44f` and zero geometry, whose 36-byte body is a `u32` generation, the
wrapped key and the wrapped IV. Each of the two is one AES-128 block decrypted in ECB
under the sign-in key. A Bell or a `MiscStatus` may come first, notably a stale
pasteboard status after a server restart, and is stepped over.

**Nothing else goes in cleartext.** A `SetEncodings` sent before the record layer was
up got no Adaptive picture at all **[measured]**.

## The record layer

Everything after the rekey, in both directions, is a record:

```text
u16 ciphertext_len
AES-128-CBC( u16 body_len || body || filler || SHA1(u32_be(seq) || everything before) )
```

- Both directions start from the rekey's key and IV, but each keeps its own CBC chain
  and its own sequence counter, from 0, for the whole session.
- The filler pads to whole blocks: `(−(2 + body_len + 20)) mod 16` bytes.
- The largest record is 65,520 bytes of ciphertext, so a body holds at most 65,498.
- Records are cut wherever they are full. A message can span several records, and one
  record can hold the end of one message and the start of the next. The bodies are put
  back together by concatenation; records are never messages.
- The Mac rotates keys only when asked with `SetEncryption` command 1. A second rekey
  cannot be followed, because records already framed under the old key would fail the
  Mac's check. Pomona ends the session instead.

## Standard mode

After the rekey, inside records:

1. **`SetPixelFormat`**: 32 bits per pixel, depth 24, little endian, true colour, maxima
   255, shifts 16/8/0. A pixel then arrives as blue, green, red, padding.
2. **`SetEncodings`**, Adaptive quality:
   `0, 1100, 1101, 1103, 1104, 1105, 1107, 1109, −223, −224, 1011, 16`.
   Full quality has `6` (zlib) where Adaptive has `1011`.
   - The Mac codes every rectangle in the **first** of zlib, ZRLE, `1000`–`1002` and
     `1011` it finds in the list, and nothing changes that but another `SetEncodings`.
   - Listing both `1105` (the layout) and `1101` (`DisplayInfo`) makes it report its
     displays as a layout.
   - The list is a promise: anything on it can arrive, so everything on it must be read
     or at least stepped over.
3. **When a display layout (`1105`) arrives**, the viewer arms `AutoFrameBufferUpdate`
   (`09 00`, a `u16` version 1, a `u32` interval in µs, then x, y, w, h as `u16`s) over
   the whole framebuffer, and sends one non-incremental `FramebufferUpdateRequest`, once
   that update has been read to its end.
   - **Every layout drops the arming**, so it is armed again after each one. A layout
     arrives at every login, lock and display change.
   - Arming before the first layout got the first picture and nothing after it
     **[measured]**.

### What arming does

- **Interval 0** pushes a change as fast as the Mac captures it. That is what Apple's
  viewer arms, and it sends no request after an update.
- **An interval above 0** paces the pushes: the Mac waits that long after each update.
  33,333 µs is about 30 updates a second.
- **`0xffffffff` turns the pushes off.** It is not a display id: `SetDisplay` picks the
  screen. iShareScreen's builder puts all-ones there, which disarms.
- **A still screen sends nothing,** armed or not. Armed, the next change is pushed
  however long the screen was still.
- **The Mac throttles itself** by how fast its socket drains: it holds the next update
  while more than a tenth of a second's worth of its estimate is still queued.
- **The Mac reads nothing while it writes an update.** At interval 0, a viewer that
  drains more slowly than the Mac captures has its keys and clicks left unread for as
  long as the screen changes: 35–104 s in one of zDesk's tests. A viewer must therefore
  read the socket all the time and never block it on decoding or painting.
- Each update carries one rectangle, read off the screen as it is sent, so a moving
  window comes as strips with no frame boundary on the wire.

## The server's stream

| Type | Message | Body |
| --- | --- | --- |
| `0x00` | FramebufferUpdate | pad, `u16` count, rectangles |
| `0x02` | Bell | nothing |
| `0x03` | ServerCutText | 3 pad, `u32` length, text |
| `0x14` | MiscStatus | pad, `u16` length, then (length 4) a `u16` 1 and a `u16` command |
| `0x1f` | pasteboard contents | see [The pasteboard](#the-pasteboard); only after a fetch |

Apple's viewer closes the connection on a message type it does not know, and so does
Pomona: there is no way to step over one.

A rectangle is a `u16` x, y, w, h and an `i32` encoding, then its body:

| Encoding | Body |
| --- | --- |
| `1011` Adaptive, `6` zlib, `16` ZRLE, `1000`–`1002` | `u32` length, then that many bytes |
| `1105` layout, `1107` vendor keysyms, `1109` keyboard source | `u16` length, then that many bytes |
| `1101` DisplayInfo | `u16` w, `u16` h, `u32` flags, `u16` count, then `0x1c` bytes per screen |
| `1104` cursor cache | `u32` raw length, `u32` compressed length, the compressed bytes |
| `1103` rekey | 36 bytes |
| `0` Raw | w × h × 4 bytes |
| `1100` cursor position, `−223` desktop size, `−224` last rectangle | nothing |

The layout, keyboard source and vendor keysyms each arrive as a one-rectangle update.
A NoMac session's first updates were the layout, the vendor keysyms, the keyboard
source, the quantization tables and then the first picture, each an update of its own
**[measured]**.

| MiscStatus command | Sent when |
| --- | --- |
| 1 | the Mac's user ends the session, just before it closes |
| 2 | the pasteboard changed |
| 3 | the Mac needs data for a flavor the viewer promised |
| 4 | heartbeat: nothing was sent to the connection for 2 s |
| 5, 6 | the displays sleep, and wake |
| 9, 10 | control allowed, observing only |
| 11, 12 | the pointer is hidden, and shown again |
| 13, 14 | busy-cursor notifications |
| 17 | a login or logout at the Mac |

## Displays

### The layout

The layout's body, after its `u16` length (which counts only what follows it), is a
`0x14`-byte header and a `0x38`-byte record per display:

| Header | Field |
| --- | --- |
| `+0x00` | `u16` version, 5 |
| `+0x02` | `u16` × 2: the desktop's size in points |
| `+0x06` | `u16` × 2: the framebuffer's size in pixels, which rectangles are addressed in |
| `+0x0a` | `u32`: the screen being sent, `0xffffffff` for all of them |
| `+0x0e` | `u32` flags: bit 0 virtual display, bit 2 on console, bit 3 lock screen, bit 4 login window. Standard sends `0x4`. |
| `+0x12` | `u16` display count, 1–25 |

| Record | Field |
| --- | --- |
| `+0x00` | `f64` native density, 1.0 or 2.0; 0.0 when the Mac could not look it up |
| `+0x08` | `f64` the server-side scale in force, 1.0 unless asked |
| `+0x10` | `u32` display id |
| `+0x14` | logical bounds in points, as `(top, left, bottom, right)` `i16`s |
| `+0x1c` | backing bounds in framebuffer pixels, the same way |
| `+0x24` | `u32` flags: bit 0 main, bit 1 in a mirror set |
| `+0x28` | 16-byte pixel format |

Bounds are edges, not an origin and a size. A NoMac Mac sends one 1440×900 display at
density 1 **[measured]**.

### Several screens, and scaling

- **`SetDisplay`** (`0d`, a `u8` "all combined", a `u16` 0, a `u32` id) picks one
  physical screen, or all of them in one framebuffer. The next layout confirms it.
- **`SetServerScaling`** (`08 00`, then an `f64` factor in (0, 1]) has the Mac scale the
  framebuffer before encoding. Apple's viewer asks for `min(1, client density / screen
  density)`, so a Retina Mac reaches a 1x client at its size in points. The next layout
  reports the factor in each record.
- **Pointer positions stay unscaled**: the Mac reads them in the pixels of the
  framebuffer before scaling, so a viewer divides by the factor in force.

## The picture

### Adaptive (`1011`, `0x3f3`)

Apple's own codec, decoded by zDesk's decoder unchanged (`crates/apple-adaptive`). The
body's first byte says what it is:

- **2, the quantization tables**: 129 bytes, once, before the first picture, in a 0×0
  rectangle.
- **0, a full update**: 8×8 tiles, each white, a copy of the tile before or above, black
  and white, one or two colours, DCT-coded, or a tile from a cache. DCT tiles use JPEG's
  coefficients, zigzag order, integer inverse transform and YCbCr. The luminance is
  coarse and the chrominance flat.
- **1, a partial update**: refines the last full update's tiles and caches refined ones,
  up to 64,999.

Each stream ends on a mark (`0x6d`; `mvs` for a partial update), which the decoder
checks. The first picture is deliberately coarse; the refinement follows at once on a
fast link. On a slow one, the refinement waits for the motion to stop.

zDesk's document describes the bit streams in full; `decoder.rs` is the executable form.

### Full: zlib (`6`) and ZRLE (`16`)

- **zlib**: one deflate stream for the whole session, flushed at each rectangle's end
  and never finished, inflating to w × h pixels in the session's pixel format. A
  rectangle missed is a stream lost. A resize does not restart it.
- **ZRLE**: standard RFB, one deflate stream for the session holding 64×64 tiles with
  three-byte pixels. Every tile the Mac sends is raw (subencoding 0).
- Nothing in either adapts to the link: a slow connection takes the same bytes later.

## Input

- **Keys** (`04`, a `u8` down flag, 2 pad, a `u32` X11 keysym):
  - An uppercase letter brings Shift with it, and a held Shift is stripped from a
    lowercase one. A shortcut under Caps Lock goes out as the lowercase keysym, or
    Command-Z arrives as Command-Shift-Z.
  - `Alt_L/R`, `Super_L/R` and `Hyper_L/R` arrive as **Command**, `Meta_L/R` as
    **Option**. A PC keyboard's Alt therefore goes out as Meta.
  - Option is stripped from ordinary keys unless Command is also held.
  - Insert, Pause, Scroll Lock, Print and Menu have no mapping; Num Lock arrives as
    Keypad Clear.
- **Pointer** (`05`, a `u8` mask, `u16` x and y): on 003.889 the Mac reads the mask as
  macOS button numbers, so bit 1 is **right** and bit 2 **middle**, the other way round
  from RFB.
- **Scroll**: the wheel bits scroll only for a mask of exactly `0x08` (up) or `0x10`
  (down), about two pixels a pulse. Apple's viewer sends the scroll-wheel event (`0x17`,
  kind 11) instead, when ServerInit's bitmap lists `0x17`. It is 58 bytes: type, flags,
  `u16` 54, `u16` version 1, `u16` kind 11, three `i16` line deltas, three `i32` line
  deltas in 16.16, three `i32` point deltas, `u32` scroll phase, momentum phase and
  scroll count, `u32` flags (2 is continuous), and the pointer's `u16` x and y. Up and
  left are positive. Pomona sends the point delta, a tenth of it as lines, the
  continuous flag and no phase.
- **Double-click** is decided by the Mac from clicks on the same spot within its
  double-click interval, which it reads once at login.

## The pasteboard

`AutoPasteboard` in the prelude makes the Mac send `MiscStatus` 2 when its pasteboard
changes, and 3 when it needs data for a flavor the viewer promised.

- **Fetch** (`0b`, a flags byte, 2 pad, 4 bytes the Mac echoes): asks for the contents.
- **Contents** (`1f`, both ways): `1f 00`, a promises bit, a pad, the four echoed bytes,
  `u32` uncompressed and compressed sizes, then a zlib archive (at most 100 MB).
- **The archive** is a run of items, each a `u32` flavor count and that many flavors. A
  flavor is a counted name, a reserved `u32`, a `u32` count of counted key/value tags,
  and counted data, every count a `u32`. A flavor without data is a promise; an empty
  archive clears the pasteboard.

## Not yet known

- How Adaptive behaves on a physical Mac or a Retina screen: nothing was captured there.
- Why one second of the research's Adaptive capture carried a single update.
- How the Mac's pacing responds to the latency of a long ssh path:
  see [measurements.md](measurements.md).
