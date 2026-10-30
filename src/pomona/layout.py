# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""The Mac's display layout (encoding 0x451): its screens, which one it sends,
and the framebuffer's size.

The body, after the `u16` that counts it, is a 0x14-byte header and then a
0x38-byte record per display, every number big-endian. Rectangles are edges
(top, left, bottom, right), not an origin and a size.
"""

import math
import struct
from dataclasses import dataclass

from pomona.errors import ProtocolError

_HEADER = 0x14
_RECORD = 0x38
_MAX_DISPLAYS = 25
ALL_DISPLAYS = 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class Display:
    id: int
    # The screen's own pixels per point: 1.0 or 2.0.
    density: float
    # The factor SetServerScaling put on it, 1.0 unless asked otherwise.
    server_scale: float
    # Size and position in points, in the Mac's own arrangement.
    logical: tuple[int, int, int, int]  # left, top, width, height
    # Size and position in framebuffer pixels.
    backing: tuple[int, int, int, int]  # left, top, width, height
    main: bool
    mirrored: bool


@dataclass(frozen=True, slots=True)
class Layout:
    # The framebuffer's size in pixels: what rectangles are addressed in.
    width: int
    height: int
    # The screen being sent, or None for all of them combined.
    current: int | None
    flags: int
    displays: tuple[Display, ...]

    @property
    def server_scale(self) -> float:
        """The factor the Mac scales the framebuffer by: the same in every record."""
        return self.displays[0].server_scale if self.displays else 1.0


def _edges(record: bytes, at: int) -> tuple[int, int, int, int]:
    top, left, bottom, right = struct.unpack_from(">4h", record, at)
    return left, top, max(right - left, 0), max(bottom - top, 0)


def parse(body: bytes) -> Layout:
    if len(body) < _HEADER:
        raise ProtocolError(f"a display layout of {len(body)} bytes, under its header")
    _version, _points_w, _points_h, width, height, current, flags, count = (
        struct.unpack_from(">HHHHHIIH", body)
    )
    if not 1 <= count <= _MAX_DISPLAYS:
        raise ProtocolError(f"a display layout listing {count} displays")
    if len(body) < _HEADER + count * _RECORD:
        raise ProtocolError(
            f"a display layout of {count} displays in {len(body)} bytes"
        )
    displays = []
    for index in range(count):
        record = body[_HEADER + index * _RECORD : _HEADER + (index + 1) * _RECORD]
        density, server_scale, display_id = struct.unpack_from(">ddI", record)
        logical = _edges(record, 0x14)
        backing = _edges(record, 0x1C)
        if not (math.isfinite(server_scale) and 0 < server_scale <= 1):
            server_scale = 1.0
        if density == 0.0 and logical[2]:
            # The Mac could not look the screen's mode up: derive it.
            density = backing[2] / logical[2] / server_scale
        record_flags = struct.unpack_from(">I", record, 0x24)[0]
        displays.append(
            Display(
                id=display_id,
                density=density,
                server_scale=server_scale,
                logical=logical,
                backing=backing,
                main=bool(record_flags & 0x01),
                mirrored=bool(record_flags & 0x02),
            )
        )
    return Layout(
        width=width,
        height=height,
        current=None if current == ALL_DISPLAYS else current,
        flags=flags,
        displays=tuple(displays),
    )
