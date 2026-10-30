# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

import struct

import pytest

from pomona import stream
from pomona.errors import ProtocolError


def rect(x, y, w, h, encoding, body=b""):
    return struct.pack(">HHHHi", x, y, w, h, encoding) + body


def update(*rects):
    return struct.pack(">BxH", 0, len(rects)) + b"".join(rects)


ADAPTIVE_BODY = bytes(range(10))
LAYOUT_BODY = b"layout"
CURSOR_BODY = struct.pack(">II", 100, 3) + b"zzz"
DISPLAY_INFO_BODY = struct.pack(">HHIH", 1440, 900, 0, 2) + bytes(2 * 0x1C)

SAMPLE = (
    update(
        rect(
            0,
            0,
            0,
            0,
            stream.DISPLAY_LAYOUT,
            struct.pack(">H", len(LAYOUT_BODY)) + LAYOUT_BODY,
        ),
        rect(
            8,
            16,
            32,
            24,
            stream.ADAPTIVE,
            struct.pack(">I", len(ADAPTIVE_BODY)) + ADAPTIVE_BODY,
        ),
        rect(1, 2, 3, 4, stream.CURSOR_IMAGE, CURSOR_BODY),
        rect(0, 0, 0, 0, stream.DISPLAY_INFO, DISPLAY_INFO_BODY),
        rect(5, 6, 0, 0, stream.CURSOR_POS),
        rect(0, 0, 1, 1, stream.RAW, b"\x01\x02\x03\x04"),
    )
    + struct.pack(">BxH", 0x14, 4)
    + b"\x00\x00\x00\x02"
    + b"\x02"
    + struct.pack(">BxxxI", 3, 5)
    + b"hello"
)


def check_sample(messages):
    assert len(messages) == 4
    first, misc, bell, cut = messages
    assert [r.encoding for r in first.rects] == [
        stream.DISPLAY_LAYOUT,
        stream.ADAPTIVE,
        stream.CURSOR_IMAGE,
        stream.DISPLAY_INFO,
        stream.CURSOR_POS,
        stream.RAW,
    ]
    layout, adaptive, cursor, info, pos, raw = first.rects
    assert layout.body == LAYOUT_BODY
    assert (adaptive.x, adaptive.y, adaptive.w, adaptive.h) == (8, 16, 32, 24)
    assert adaptive.body == ADAPTIVE_BODY
    assert cursor.body == CURSOR_BODY
    assert info.body == DISPLAY_INFO_BODY
    assert (pos.x, pos.y, pos.body) == (5, 6, b"")
    assert raw.body == b"\x01\x02\x03\x04"
    assert misc == stream.MiscStatus(b"\x00\x00\x00\x02")
    assert bell == stream.Bell()
    assert cut == stream.ServerCutText(b"hello")


def test_whole_stream_at_once():
    parser = stream.Parser()
    check_sample(parser.feed(SAMPLE))
    assert parser.pending == 0


def test_one_byte_at_a_time():
    parser = stream.Parser()
    messages = []
    for i in range(len(SAMPLE)):
        messages += parser.feed(SAMPLE[i : i + 1])
    check_sample(messages)
    assert parser.pending == 0


def test_last_rect_ends_an_update_early():
    data = struct.pack(">BxH", 0, 0xFFFF) + rect(0, 0, 0, 0, stream.LAST_RECT) + b"\x02"
    messages = stream.Parser().feed(data)
    assert messages == [
        stream.FramebufferUpdate((stream.Rect(0, 0, 0, 0, stream.LAST_RECT, b""),)),
        stream.Bell(),
    ]


def test_unknown_encoding_is_an_error():
    with pytest.raises(
        ProtocolError, match="unknown encoding 12345 at stream offset 16"
    ):
        stream.Parser().feed(update(rect(0, 0, 1, 1, 12345)))


def test_unknown_message_is_an_error():
    parser = stream.Parser()
    parser.feed(b"\x02")
    with pytest.raises(ProtocolError, match="0x7f at stream offset 1"):
        parser.feed(b"\x7f")
