# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Plain-Python decoders for zlib, ZRLE and Raw, written apart from the native
ones so the tests can check them against the Mac's real captures.

They only need to read what a Mac sends: ZRLE here knows raw and solid tiles,
the only ones seen from a Mac.
"""

import zlib

from pomona import stream

TILE = 64


class Framebuffer:
    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.pixels = bytearray(b"\0\0\0\xff" * (width * height))
        self._zlib = zlib.decompressobj()
        self._zrle = zlib.decompressobj()

    def draw(self, rect: stream.Rect) -> None:
        if rect.encoding == stream.RAW:
            self._rows(rect.x, rect.y, rect.w, rect.h, rect.body, 4)
        elif rect.encoding == stream.ZLIB:
            data = self._zlib.decompress(rect.body)
            assert len(data) == rect.w * rect.h * 4
            self._rows(rect.x, rect.y, rect.w, rect.h, data, 4)
        elif rect.encoding == stream.ZRLE:
            self._zrle_tiles(rect, self._zrle.decompress(rect.body))

    def _rows(self, x, y, w, h, data, size) -> None:
        """Rows of blue, green, red (and padding when size is 4)."""
        for row in range(h):
            src = data[row * w * size : (row + 1) * w * size]
            rgba = bytearray(b"\xff" * (w * 4))
            rgba[0::4] = src[2::size]
            rgba[1::4] = src[1::size]
            rgba[2::4] = src[0::size]
            start = ((y + row) * self.width + x) * 4
            self.pixels[start : start + w * 4] = rgba

    def _zrle_tiles(self, rect: stream.Rect, data: bytes) -> None:
        at = 0
        for ty in range(0, rect.h, TILE):
            for tx in range(0, rect.w, TILE):
                tw, th = min(TILE, rect.w - tx), min(TILE, rect.h - ty)
                kind = data[at]
                at += 1
                if kind == 0:
                    size = tw * th * 3
                    tile = data[at : at + size]
                    at += size
                elif kind == 1:
                    tile = data[at : at + 3] * (tw * th)
                    at += 3
                else:
                    raise NotImplementedError(f"ZRLE subencoding {kind}")
                self._rows(rect.x + tx, rect.y + ty, tw, th, tile, 3)
        assert at == len(data)
