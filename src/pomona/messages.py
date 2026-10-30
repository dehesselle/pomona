# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""The messages Pomona sends a Mac, as bytes.

Byte layouts follow what zDesk measured against macOS 26 (see its
`docs/apple-vnc-889.md`); where a published description disagrees, the
measurement wins.
"""

import struct

from pomona import stream

# What a Standard session advertises. The Mac codes every picture in the first
# of zlib, ZRLE and its own codecs that it finds, and listing both the layout
# and DisplayInfo makes it report its displays as a layout. Every entry can
# arrive, so every entry must be read or stepped over.
_BASE_ENCODINGS = (
    stream.RAW,
    stream.CURSOR_POS,
    stream.DISPLAY_INFO,
    stream.REKEY,
    stream.CURSOR_IMAGE,
    stream.DISPLAY_LAYOUT,
    stream.VENDOR_KEYSYMS,
    stream.KEYBOARD_SOURCE,
    stream.DESKTOP_SIZE,
    stream.LAST_RECT,
)
ADAPTIVE_ENCODINGS = (*_BASE_ENCODINGS, stream.ADAPTIVE, stream.ZRLE)
FULL_ENCODINGS = (*_BASE_ENCODINGS, stream.ZLIB, stream.ZRLE)

# Client message types.
SET_PIXEL_FORMAT = 0x00
SET_ENCODINGS = 0x02
FRAMEBUFFER_UPDATE_REQUEST = 0x03
KEY_EVENT = 0x04
POINTER_EVENT = 0x05
SET_SERVER_SCALING = 0x08
AUTO_FRAMEBUFFER_UPDATE = 0x09
SET_MODE = 0x0A
SET_DISPLAY = 0x0D
SET_ENCRYPTION = 0x12
AUTO_PASTEBOARD = 0x15
EVENT_2 = 0x17
VIEWER_INFO = 0x21

# AutoFrameBufferUpdate's interval that turns the Mac's pushes off.
PUSHES_OFF = 0xFFFFFFFF


def viewer_info() -> bytes:
    """Who is viewing: Apple's Screen Sharing 6.1 on macOS 26.6.2.

    Two numeric version triples and the bitmap of server messages the viewer
    handles, 66 bytes in all, as Apple's viewer sends it. A body of another
    size makes the Mac swallow the next message.
    """
    msg = bytearray(66)
    struct.pack_into(">BxH", msg, 0, VIEWER_INFO, 62)
    # application class 1, id 2 (Screen Sharing), version 6.1.0
    struct.pack_into(">HIIII", msg, 4, 1, 2, 6, 1, 0)
    # macOS 26.6.2: the Mac handles keys from a viewer it thinks older than
    # 10.15 the old way.
    struct.pack_into(">III", msg, 22, 26, 6, 2)
    # The command bitmap Apple's viewer sends, in the last 32 bytes.
    for at, value in ((34, 0xB0), (36, 0x0C), (37, 0x03), (38, 0x90), (44, 0x40)):
        msg[at] = value
    return bytes(msg)


def set_mode_control() -> bytes:
    """Ask for a controlling session, as Apple's viewer does before the
    pasteboard is turned on."""
    return bytes((SET_MODE, 0, 0, 1))


def auto_pasteboard(enabled: bool) -> bytes:
    """Have the Mac say when its pasteboard changes. Only works in cleartext,
    before the record layer."""
    return bytes((AUTO_PASTEBOARD, 0, 0, 1 if enabled else 2, 0, 0, 0, 0))


def set_encryption_start() -> bytes:
    """SetEncryption command 1, method 1: the Mac answers with the rekey."""
    return bytes.fromhex("120000010001000100000001")


def set_encryption_decrypt_inbound() -> bytes:
    """SetEncryption command 2: the Mac decrypts what it receives from now on."""
    return bytes.fromhex("1200000200010000")


def set_pixel_format() -> bytes:
    """32 bits per pixel, depth 24, little endian, true colour, red at bit 16:
    each pixel arrives as B, G, R, padding."""
    return struct.pack(
        ">BxxxBBBBHHHBBBxxx", SET_PIXEL_FORMAT, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0
    )


def set_encodings(encodings: tuple[int, ...]) -> bytes:
    return struct.pack(
        f">BxH{len(encodings)}i", SET_ENCODINGS, len(encodings), *encodings
    )


def auto_framebuffer_update(interval_us: int, width: int, height: int) -> bytes:
    """Arm the Mac's pushes over the whole framebuffer: at most one update each
    `interval_us`, 0 for as fast as it captures, `PUSHES_OFF` for none."""
    return struct.pack(
        ">BxHIHHHH", AUTO_FRAMEBUFFER_UPDATE, 1, interval_us, 0, 0, width, height
    )


def framebuffer_update_request(
    incremental: bool, x: int, y: int, width: int, height: int
) -> bytes:
    return struct.pack(
        ">BBHHHH", FRAMEBUFFER_UPDATE_REQUEST, incremental, x, y, width, height
    )


def key_event(keysym: int, down: bool) -> bytes:
    return struct.pack(">BBxxI", KEY_EVENT, down, keysym)


def pointer_event(mask: int, x: int, y: int) -> bytes:
    """`mask` as the Mac reads it on 003.889: bit 0 left, bit 1 right, bit 2
    middle (macOS button numbers), bits 3 and 4 a wheel pulse up and down."""
    return struct.pack(">BBHH", POINTER_EVENT, mask, x, y)


def scroll_wheel(dx: int, dy: int, x: int, y: int) -> bytes:
    """A scroll of `dx`, `dy` points, positive right and down, at `x`, `y`.

    The scroll-wheel kind (11) of the second event message, 58 bytes. The Mac
    counts up and left as positive, so both go out negated: the distance as
    the point delta, a tenth of it as the line delta, the continuous flag, and
    no gesture phase.
    """
    points_per_line = 10
    continuous = 0x02
    axes = (-dx, -dy, 0)
    lines = [_toward_zero(p, points_per_line) for p in axes]
    fixed = [_toward_zero(p * 65536, points_per_line) for p in axes]  # 16.16
    return struct.pack(
        ">BxHHH3h3i3iIIIIHH",
        EVENT_2,
        54,
        1,
        11,
        *lines,
        *fixed,
        *axes,
        0,
        0,
        0,
        continuous,
        x,
        y,
    )


def _toward_zero(dividend: int, divisor: int) -> int:
    quotient = abs(dividend) // divisor
    return -quotient if dividend < 0 else quotient


def set_display(display_id: int | None) -> bytes:
    """Share one physical screen, or all of them combined for None."""
    return struct.pack(">BBHI", SET_DISPLAY, display_id is None, 0, display_id or 0)


def set_server_scaling(factor: float) -> bytes:
    """Have the Mac scale its framebuffer by `factor`, in (0, 1], before encoding."""
    if not 0 < factor <= 1:
        raise ValueError(f"a server scaling factor of {factor}, outside (0, 1]")
    return struct.pack(">Bxd", SET_SERVER_SCALING, factor)
