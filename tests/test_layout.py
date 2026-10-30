# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

import struct
from pathlib import Path

import pytest

from pomona import layout, stream
from pomona.errors import ProtocolError

FIXTURE = Path(__file__).parent / "fixtures" / "adaptive.rfb"


def recorded_layout() -> bytes:
    """The layout a NoMac Mac sent first, from the fixture."""
    first = stream.Parser().feed(FIXTURE.read_bytes()[:4096])[0]
    (rect,) = [r for r in first.rects if r.encoding == stream.DISPLAY_LAYOUT]
    return rect.body


def test_a_recorded_layout():
    parsed = layout.parse(recorded_layout())
    assert (parsed.width, parsed.height) == (1440, 900)
    assert len(parsed.displays) == 1
    (display,) = parsed.displays
    assert display.backing == (0, 0, 1440, 900)
    assert display.server_scale == 1.0
    assert display.main
    assert parsed.server_scale == 1.0


def record(density, scale, display_id, logical, backing, flags=1):
    def edges(left, top, width, height):
        return struct.pack(">4h", top, left, top + height, left + width)

    return (
        struct.pack(">ddI", density, scale, display_id)
        + edges(*logical)
        + edges(*backing)
        + struct.pack(">I", flags)
        + bytes(16)
    )


def test_two_displays_combined_and_scaled():
    header = struct.pack(">HHHHHIIH", 5, 2720, 900, 2160, 675, 0xFFFFFFFF, 4, 2)
    body = (
        header
        + record(2.0, 0.75, 4, (0, 0, 1440, 900), (0, 0, 2160, 1350))
        + record(0.0, 0.75, 1, (1440, 0, 1280, 800), (2160, 0, 960, 600), flags=0)
    )
    parsed = layout.parse(body)
    assert parsed.current is None
    assert (parsed.width, parsed.height) == (2160, 675)
    first, second = parsed.displays
    assert (first.id, first.density, first.main) == (4, 2.0, True)
    # A density of 0.0 is derived from the rects and the scale.
    assert second.density == pytest.approx(1.0)
    assert second.backing == (2160, 0, 960, 600)
    assert parsed.server_scale == 0.75


def test_a_short_layout_is_refused():
    with pytest.raises(ProtocolError):
        layout.parse(recorded_layout()[:-1])
