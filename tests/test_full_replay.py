# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Full quality's codecs against the Mac's own streams.

`fixtures/zlib.rfb` and `fixtures/zrle.rfb` are the starts of decrypted RFB
streams recorded from a NoMac Mac (macOS 26.6, 1440×900) showing a scrolling
Terminal, one listing zlib first and one ZRLE: each opens with the whole
screen, then the scrolling window's updates. They were cut after their last
whole message before 2.4 MB.
"""

from pathlib import Path

import pytest
from reference import Framebuffer

from pomona import stream
from pomona._native import Picture, StreamError
from pomona.cursor import CursorCache

FIXTURES = Path(__file__).parent / "fixtures"


def updates(name: str) -> list[stream.FramebufferUpdate]:
    messages = stream.Parser().feed((FIXTURES / name).read_bytes())
    return [m for m in messages if isinstance(m, stream.FramebufferUpdate)]


@pytest.mark.parametrize(
    ("name", "encoding"), [("zlib.rfb", stream.ZLIB), ("zrle.rfb", stream.ZRLE)]
)
def test_native_matches_the_reference_after_every_update(name, encoding):
    picture, reference = Picture(1440, 900), Framebuffer(1440, 900)
    drawn = 0
    for update in updates(name):
        for rect in update.rects:
            if rect.encoding == encoding:
                assert picture.decode(
                    rect.encoding, rect.x, rect.y, rect.w, rect.h, rect.body
                )
                reference.draw(rect)
                drawn += 1
        assert picture.snapshot() == reference.pixels
    assert drawn > 20
    # Not a black screen: the first update painted all of it.
    assert len(set(reference.pixels[0::4])) > 50


def test_a_stream_missing_its_start_is_a_stream_error():
    rects = [
        r for u in updates("zlib.rfb") for r in u.rects if r.encoding == stream.ZLIB
    ]
    picture = Picture(1440, 900)
    with pytest.raises(StreamError):
        later = rects[1]
        picture.decode(later.encoding, later.x, later.y, later.w, later.h, later.body)


def test_a_rectangle_off_the_framebuffer_is_a_plain_value_error():
    rect = next(
        r for u in updates("zlib.rfb") for r in u.rects if r.encoding == stream.ZLIB
    )
    picture = Picture(100, 100)
    with pytest.raises(ValueError, match="off the framebuffer") as caught:
        picture.decode(rect.encoding, rect.x, rect.y, rect.w, rect.h, rect.body)
    assert not isinstance(caught.value, StreamError)


def test_the_recorded_cursor():
    cache = CursorCache()
    (rect,) = [
        r
        for u in updates("zlib.rfb")
        for r in u.rects
        if r.encoding == stream.CURSOR_IMAGE
    ]
    shape = cache.accept(rect.x, rect.y, rect.w, rect.h, rect.body)
    assert (shape.width, shape.height) == (28, 40)
    assert len(shape.rgba) == 28 * 40 * 4
    alpha = set(shape.rgba[3::4])
    # An arrow: transparent around it, opaque in it, smooth at its edges.
    assert 0 in alpha and 255 in alpha and len(alpha) > 2
    # A select of the same id returns it; an unknown id returns nothing.
    cache_id = rect.body[:4]
    assert cache.accept(0, 0, 0, 0, cache_id + bytes(4)) == shape
    assert cache.accept(0, 0, 0, 0, b"\0\0\0\x99" + bytes(4)) is None
