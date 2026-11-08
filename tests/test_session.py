# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""A whole session against the fake Mac: sign-in, record layer, replay."""

import hashlib
import struct
import threading
from pathlib import Path

import fakemac
import pytest
from test_adaptive_replay import CHECKPOINTS

from pomona import messages, stream
from pomona.errors import AuthenticationError, EndedAtMacError
from pomona.session import LEFT, MIDDLE, RIGHT, KeyboardSource, connect

FIXTURE = (Path(__file__).parent / "fixtures" / "adaptive.rfb").read_bytes()
UPDATES = sum(
    isinstance(m, stream.FramebufferUpdate) for m in stream.Parser().feed(FIXTURE)
)


def connect_to(mac: fakemac.FakeMac, **kwargs):
    kwargs.setdefault("user", mac.user)
    kwargs.setdefault("password", mac.password)
    return connect("127.0.0.1", mac.port, timeout=10, **kwargs)


def test_replay_draws_the_recorded_picture_and_arms_on_the_layout():
    mac = fakemac.FakeMac(FIXTURE)
    seen = 0
    done = threading.Event()

    def count(update):
        nonlocal seen
        seen += 1
        if seen == UPDATES:
            done.set()

    with connect_to(mac, on_update=count) as session:
        assert session.server.name == "Fake Mac"
        assert session.server.flags & 0x02
        assert done.wait(30), f"{seen} of {UPDATES} updates"
        assert session.error is None
        assert (
            hashlib.sha256(session.picture.snapshot()).hexdigest() == CHECKPOINTS[196]
        )
        assert (session.layout.width, session.layout.height) == (1440, 900)
    mac.join()

    pixel_format, encodings, arm, request = mac.received[:4]
    assert pixel_format == messages.set_pixel_format()
    assert encodings == messages.set_encodings(messages.ADAPTIVE_ENCODINGS)
    assert arm == messages.auto_framebuffer_update(0, 1440, 900)
    assert request == messages.framebuffer_update_request(False, 0, 0, 1440, 900)


def test_full_quality_draws_zlib_and_keeps_the_cursor():
    from reference import Framebuffer

    recorded = (Path(__file__).parent / "fixtures" / "zlib.rfb").read_bytes()
    replayed = stream.Parser().feed(recorded)
    expected = Framebuffer(1440, 900)
    for message in replayed:
        if isinstance(message, stream.FramebufferUpdate):
            for rect in message.rects:
                expected.draw(rect)
    total = sum(isinstance(m, stream.FramebufferUpdate) for m in replayed)
    mac = fakemac.FakeMac(recorded)
    seen = 0
    done = threading.Event()

    def count(update):
        nonlocal seen
        seen += 1
        if seen == total:
            done.set()

    with connect_to(mac, quality="full", on_update=count) as session:
        assert done.wait(30), f"{seen} of {total} updates"
        assert session.error is None
        assert session.picture.snapshot() == expected.pixels
        assert (session.cursor.width, session.cursor.height) == (28, 40)
        assert session.cursor_position is not None
    mac.join()
    assert mac.received[1] == messages.set_encodings(messages.FULL_ENCODINGS)


def test_a_wrong_password_is_refused():
    mac = fakemac.FakeMac()
    with pytest.raises(AuthenticationError, match="Remote Management"):
        connect_to(mac, password="wrong")
    mac.join()


def test_input_goes_out_in_order_with_the_buttons_swapped():
    mac = fakemac.FakeMac()
    with connect_to(mac) as session:
        session.key(0x61, True)
        session.key(0x61, False)
        session.pointer(10, 20, LEFT | RIGHT)
        session.pointer(10, 20, MIDDLE)
        session.scroll(0, 30, 10, 20)
    mac.join()
    assert mac.received[2:] == [
        messages.key_event(0x61, True),
        messages.key_event(0x61, False),
        messages.pointer_event(0x03, 10, 20),
        messages.pointer_event(0x04, 10, 20),
        messages.scroll_wheel(0, 30, 10, 20),
    ]


def test_a_mac_without_the_scroll_message_gets_a_wheel_pulse():
    mac = fakemac.FakeMac(commands=fakemac.NO_SCROLL)
    with connect_to(mac) as session:
        session.scroll(0, -30, 10, 20)
    mac.join()
    assert mac.received[2:] == [
        messages.pointer_event(0x08, 10, 20),
        messages.pointer_event(0x00, 10, 20),
    ]


def test_the_session_ends_when_the_mac_hangs_up():
    mac = fakemac.FakeMac(FIXTURE[:5000], hang_up=True)
    session = connect_to(mac)
    assert session.wait(10)
    # A clean end of stream, or, when our last messages went unread, a reset
    # (Linux) or an abort (Windows).
    assert "closed the connection" in str(session.error) or isinstance(
        session.error, ConnectionError
    )
    session.close()
    mac.join()


def first_update():
    """The fixture up to the end of its first update."""
    parser, fed = stream.Parser(), 0
    while True:
        parsed = parser.feed(FIXTURE[fed : fed + 1000])
        fed += 1000
        if any(isinstance(m, stream.FramebufferUpdate) for m in parsed):
            return FIXTURE[: fed - parser.pending]


def test_a_session_has_not_ended_while_its_reader_still_draws():
    # The writer finds the connection dead while the reader is still in the
    # middle of what came before: a session following this one must not
    # start on the picture yet.
    mac = fakemac.FakeMac(first_update(), hang_up=True)
    drawing, release = threading.Event(), threading.Event()

    def hold(_update):
        drawing.set()
        release.wait(10)

    session = connect_to(mac, on_update=hold)
    assert drawing.wait(10)
    for _ in range(200):
        if session.closed:
            break
        session.key(0x61, True)
        threading.Event().wait(0.05)
    assert session.closed, "the writer never found the connection dead"
    assert not session.wait(0.3)
    release.set()
    assert session.wait(10)
    assert isinstance(session.error, OSError)
    session.close()
    mac.join()


def test_closing_ends_the_session_without_an_error():
    mac = fakemac.FakeMac()
    session = connect_to(mac)
    assert not session.wait(0.2)
    session.close()
    mac.join()
    assert session.closed
    assert session.error is None


def test_a_retina_mac_is_asked_once_to_scale_to_the_client():
    from test_layout import record

    body = struct.pack(
        ">HHHHHIIH", 5, 1440, 900, 2880, 1800, 0xFFFFFFFF, 4, 1
    ) + record(2.0, 1.0, 1, (0, 0, 1440, 900), (0, 0, 2880, 1800))
    layout_update = (
        struct.pack(">BxH", 0, 1)
        + struct.pack(">HHHHi", 0, 0, 0, 0, stream.DISPLAY_LAYOUT)
        + struct.pack(">H", len(body))
        + body
    )
    mac = fakemac.FakeMac(layout_update)
    with connect_to(mac) as session:
        for _ in range(100):
            if session.layout is not None:
                break
            threading.Event().wait(0.05)
        assert (session.picture.width, session.picture.height) == (2880, 1800)
        session.set_client_scale(1.25)
        session.set_client_scale(1.25)
    mac.join()
    assert mac.received[2:] == [
        messages.auto_framebuffer_update(0, 2880, 1800),
        messages.framebuffer_update_request(False, 0, 0, 2880, 1800),
        messages.set_server_scaling(0.625),
    ]


def test_a_session_the_mac_s_user_ends_says_so():
    mac = fakemac.FakeMac(bytes.fromhex("1400000400010001"), hang_up=True)
    session = connect_to(mac)
    assert session.wait(10)
    assert isinstance(session.error, EndedAtMacError)
    session.close()
    mac.join()


def test_the_mac_s_keyboard_source_is_kept():
    name = b"com.apple.keylayout.German"
    body = struct.pack(">HIH", 1, 1, len(name)) + name
    update = (
        struct.pack(">BxH", 0, 1)
        + struct.pack(">HHHHi", 0, 0, 0, 0, stream.KEYBOARD_SOURCE)
        + struct.pack(">H", len(body))
        + body
    )
    mac = fakemac.FakeMac(update)
    with connect_to(mac) as session:
        for _ in range(100):
            if session.keyboard_source is not None:
                break
            threading.Event().wait(0.05)
    mac.join()
    assert session.keyboard_source == KeyboardSource("com.apple.keylayout.German", True)
