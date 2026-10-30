# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Replay a recorded Adaptive stream through the parser and the native decoder.

`fixtures/adaptive.rfb` is the start of a decrypted RFB stream from a NoMac Mac
(macOS 26.6, 1440×900) showing a scrolling Terminal: everything after the
record layer came up, cut after the 196th message. The hashes were checked
against a pure-Python port of the decoder, so they pin down the pixels.
"""

import hashlib
from pathlib import Path

from pomona import stream
from pomona._native import ADAPTIVE, Picture

FIXTURE = Path(__file__).parent / "fixtures" / "adaptive.rfb"

# The framebuffer's SHA-256 after a message: the first full update (coarse),
# after the refinement passes over it, and at the end of the fixture.
CHECKPOINTS = {
    5: "64ef33d2763b5025a017a1c8f0bb064307dab58a5dce167aee7f00a3757a6209",
    13: "1d6376d96fb0f9251204e542366bf94d40932862c20a9f194c80761fe4e6fbca",
    196: "3b31c7e23acda07614b92cbaee7480c71fff375e2b04f597d6b88aec4b0744de",
}


def test_stream_constant_matches_native():
    assert stream.ADAPTIVE == ADAPTIVE


def test_replay_reaches_the_recorded_pictures():
    messages = stream.Parser().feed(FIXTURE.read_bytes())
    assert len(messages) == max(CHECKPOINTS)
    picture = Picture(1440, 900)
    seen = {}
    for number, message in enumerate(messages, 1):
        if isinstance(message, stream.FramebufferUpdate):
            for r in message.rects:
                if r.encoding == stream.ADAPTIVE:
                    picture.decode(r.encoding, r.x, r.y, r.w, r.h, r.body)
        if number in CHECKPOINTS:
            seen[number] = hashlib.sha256(picture.snapshot()).hexdigest()
    assert seen == CHECKPOINTS


def test_resize_blackens_the_picture():
    picture = Picture(2, 1)
    picture.resize(1, 2)
    assert (picture.width, picture.height) == (1, 2)
    assert picture.snapshot() == b"\x00\x00\x00\xff" * 2
