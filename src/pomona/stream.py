# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Whole server messages out of the Mac's RFB stream.

Above the record layer the stream has no framing of its own: records are cut
wherever they are full, and a message ends only where its own fields say. So
`Parser` takes the decrypted bytes as they come and hands out each message once
all of it is there. A rectangle of an encoding it does not know ends the
session, because there is no way to step past it.
"""

import struct
from dataclasses import dataclass

from pomona.errors import ProtocolError

# Encodings a rectangle may arrive in, with Apple's own as the Mac numbers them.
RAW = 0
ZLIB = 6
ZRLE = 16
APPLE_1000 = 0x3E8
APPLE_1001 = 0x3E9
APPLE_1002 = 0x3EA
ADAPTIVE = 0x3F3
CURSOR_POS = 0x44C
DISPLAY_INFO = 0x44D
REKEY = 0x44F
CURSOR_IMAGE = 0x450
DISPLAY_LAYOUT = 0x451
VENDOR_KEYSYMS = 0x453
KEYBOARD_SOURCE = 0x455
DESKTOP_SIZE = -223
LAST_RECT = -224

# Server message types.
FRAMEBUFFER_UPDATE = 0x00
BELL = 0x02
SERVER_CUT_TEXT = 0x03
MISC_STATUS = 0x14

_U16 = struct.Struct(">H")
_U32 = struct.Struct(">I")
_RECT = struct.Struct(">HHHHi")

# Bodies that start with their own length, which is not part of `Rect.body`.
_U32_LENGTH = frozenset({ZLIB, ZRLE, APPLE_1000, APPLE_1001, APPLE_1002, ADAPTIVE})
_U16_LENGTH = frozenset({DISPLAY_LAYOUT, VENDOR_KEYSYMS, KEYBOARD_SOURCE})
_NO_BODY = frozenset({CURSOR_POS, DESKTOP_SIZE, LAST_RECT})
_REKEY_LENGTH = 36
# The largest rectangle body read: a full 5K screen of raw pixels fits.
MAX_BODY = 64 << 20
_DISPLAY_INFO_HEADER = 10
_DISPLAY_INFO_SCREEN = 0x1C


@dataclass(frozen=True, slots=True)
class Rect:
    """One rectangle of a framebuffer update.

    `body` is what follows the header, minus the `u32` or `u16` length that
    some encodings put first. The cursor image keeps its two lengths.
    """

    x: int
    y: int
    w: int
    h: int
    encoding: int
    body: bytes


@dataclass(frozen=True, slots=True)
class FramebufferUpdate:
    rects: tuple[Rect, ...]


@dataclass(frozen=True, slots=True)
class Bell:
    pass


@dataclass(frozen=True, slots=True)
class ServerCutText:
    text: bytes


@dataclass(frozen=True, slots=True)
class MiscStatus:
    body: bytes


Message = FramebufferUpdate | Bell | ServerCutText | MiscStatus


class Parser:
    """Feed it the decrypted stream; it returns the messages completed so far."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        # Bytes of the stream consumed before the buffer's start, for errors.
        self._offset = 0

    def feed(self, data: bytes) -> list[Message]:
        self._buffer += data
        messages = []
        position = 0
        while (parsed := self._message(position)) is not None:
            message, position = parsed
            messages.append(message)
        del self._buffer[:position]
        self._offset += position
        return messages

    @property
    def pending(self) -> int:
        """Bytes received that do not yet make a whole message."""
        return len(self._buffer)

    def _message(self, start: int) -> tuple[Message, int] | None:
        """The message at `start` and where the next begins, or None if it is not all here."""
        buffer = self._buffer
        if start >= len(buffer):
            return None
        kind = buffer[start]
        if kind == FRAMEBUFFER_UPDATE:
            return self._update(start)
        if kind == MISC_STATUS:
            # type, pad, u16 length, body
            if start + 4 > len(buffer):
                return None
            end = start + 4 + _U16.unpack_from(buffer, start + 2)[0]
            return (
                None
                if end > len(buffer)
                else (MiscStatus(bytes(buffer[start + 4 : end])), end)
            )
        if kind == BELL:
            return Bell(), start + 1
        if kind == SERVER_CUT_TEXT:
            # type, 3 pad, u32 length, text
            if start + 8 > len(buffer):
                return None
            end = start + 8 + _U32.unpack_from(buffer, start + 4)[0]
            return (
                None
                if end > len(buffer)
                else (ServerCutText(bytes(buffer[start + 8 : end])), end)
            )
        raise ProtocolError(
            f"unknown server message 0x{kind:02x} at stream offset {self._offset + start}"
        )

    def _update(self, start: int) -> tuple[FramebufferUpdate, int] | None:
        buffer = self._buffer
        # type, pad, u16 count, then the rectangles
        if start + 4 > len(buffer):
            return None
        count = _U16.unpack_from(buffer, start + 2)[0]
        position = start + 4
        rects = []
        for _ in range(count):
            if position + _RECT.size > len(buffer):
                return None
            x, y, w, h, encoding = _RECT.unpack_from(buffer, position)
            position += _RECT.size
            framing = self._body(encoding, w, h, position)
            if framing is None:
                return None
            skip, end = framing
            if end > len(buffer):
                return None
            rects.append(
                Rect(x, y, w, h, encoding, bytes(buffer[position + skip : end]))
            )
            position = end
            if encoding == LAST_RECT:
                break
        return FramebufferUpdate(tuple(rects)), position

    def _body(
        self, encoding: int, w: int, h: int, start: int
    ) -> tuple[int, int] | None:
        """How many length bytes the body at `start` begins with, and where it
        ends; None if its length is not all here yet."""
        buffer = self._buffer
        if encoding in _U32_LENGTH:
            if start + 4 > len(buffer):
                return None
            length = _U32.unpack_from(buffer, start)[0]
            if length > MAX_BODY:
                # Rather than buffer whatever a broken stream claims.
                raise ProtocolError(
                    f"a {length}-byte rectangle at stream offset {self._offset + start}"
                )
            return 4, start + 4 + length
        if encoding in _U16_LENGTH:
            if start + 2 > len(buffer):
                return None
            return 2, start + 2 + _U16.unpack_from(buffer, start)[0]
        if encoding in _NO_BODY:
            return 0, start
        if encoding == CURSOR_IMAGE:
            # u32 raw length, u32 compressed length, the compressed bytes
            if start + 8 > len(buffer):
                return None
            return 0, start + 8 + _U32.unpack_from(buffer, start + 4)[0]
        if encoding == DISPLAY_INFO:
            # u16 width, u16 height, u32 flags, u16 count, then each screen
            if start + _DISPLAY_INFO_HEADER > len(buffer):
                return None
            count = _U16.unpack_from(buffer, start + 8)[0]
            return 0, start + _DISPLAY_INFO_HEADER + count * _DISPLAY_INFO_SCREEN
        if encoding == REKEY:
            return 0, start + _REKEY_LENGTH
        if encoding == RAW:
            return 0, start + w * h * 4
        raise ProtocolError(
            f"a rectangle of unknown encoding {encoding} at stream offset {self._offset + start}"
        )
