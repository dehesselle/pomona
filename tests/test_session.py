# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""A whole session against the fake Mac: sign-in, record layer, replay."""

import hashlib
import threading
from pathlib import Path

import fakemac
import pytest
from test_adaptive_replay import CHECKPOINTS

from pomona import messages, stream
from pomona.errors import AuthenticationError
from pomona.session import LEFT, MIDDLE, RIGHT, connect

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
    assert "closed the connection" in str(session.error)
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
