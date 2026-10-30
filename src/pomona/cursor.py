# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""The Mac's pointer shapes (encoding 0x450), which it caches by id.

A rectangle's x and y are the shape's hotspot and its w and h its size, in
points. The body is a `u32` cache id, a `u32` compressed length and that many
bytes. A length of 0 selects a shape stored earlier under the id; otherwise
the bytes are a zlib stream of their own, flushed but never finished, holding
w × h pixels of blue, green, red and padding, and then a separate plane of
w × h alpha bytes.
"""

import struct
import zlib
from dataclasses import dataclass

from pomona.errors import ProtocolError

# The largest shape kept; the Mac's are a few dozen points.
MAX_EDGE = 512


@dataclass(frozen=True, slots=True)
class Cursor:
    width: int
    height: int
    hotspot: tuple[int, int]
    # Row after row, each pixel R, G, B, A, straight (not premultiplied) alpha.
    rgba: bytes


class CursorCache:
    def __init__(self) -> None:
        self._shapes: dict[int, Cursor] = {}

    def accept(self, x: int, y: int, w: int, h: int, body: bytes) -> Cursor | None:
        """The shape a cursor rectangle stores or selects; None for a select
        of an id never stored. Raises ProtocolError for a shape that does not
        decode, which leaves the stream intact."""
        cache_id, length = struct.unpack_from(">II", body)
        if length == 0:
            return self._shapes.get(cache_id)
        if not (0 < w <= MAX_EDGE and 0 < h <= MAX_EDGE):
            raise ProtocolError(f"a {w}x{h} cursor")
        pixels = w * h
        try:
            # Flushed but never finished, so read as a stream, not a whole.
            raw = zlib.decompressobj().decompress(body[8 : 8 + length])
        except zlib.error as error:
            raise ProtocolError(
                f"cursor {cache_id} does not inflate: {error}"
            ) from None
        if len(raw) != pixels * 5:
            raise ProtocolError(
                f"cursor {cache_id} is {len(raw)} bytes, where {w}x{h} takes {pixels * 5}"
            )
        rgba = bytearray(pixels * 4)
        rgba[0::4] = raw[2 : pixels * 4 : 4]
        rgba[1::4] = raw[1 : pixels * 4 : 4]
        rgba[2::4] = raw[0 : pixels * 4 : 4]
        rgba[3::4] = raw[pixels * 4 :]
        shape = Cursor(w, h, (x, y), bytes(rgba))
        self._shapes[cache_id] = shape
        return shape
