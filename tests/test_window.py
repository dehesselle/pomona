# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""The window's side in Python, without a window: events and reconnecting."""

import hashlib
import threading

import fakemac
from test_adaptive_replay import CHECKPOINTS
from test_session import FIXTURE, UPDATES, connect_to

from pomona import stream
from pomona._native import Picture
from pomona.window import Viewer


class Recorder:
    """Stands in for a session and keeps what it was asked."""

    def __init__(self):
        self.calls = []
        self.picture = Picture(4, 4)
        self.pasteboard = None

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


def test_the_window_input_reaches_the_session():
    session = Recorder()
    on_event = Viewer(session).on_event
    on_event("opened", "WARP", 1.5)
    on_event("key", 0xFFE7, True)
    on_event("pointer", 10, 20, 1)
    on_event("scroll", 0, 40, 10, 20)
    on_event("pasteboard", "copied")
    on_event("scale", 2.0)
    on_event("something new", 1)
    assert session.calls == [
        ("set_client_scale", 1.5),
        ("key", 0xFFE7, True),
        ("pointer", 10, 20, 1),
        ("scroll", 0, 40, 10, 20),
        ("set_pasteboard", "copied"),
        ("set_client_scale", 2.0),
    ]


def wait_for(condition, seconds=10):
    for _ in range(int(seconds / 0.05)):
        if condition():
            return True
        threading.Event().wait(0.05)
    return False


def test_a_dropped_session_is_followed_on_the_same_picture():
    dropping = fakemac.FakeMac(hang_up=True)
    following = fakemac.FakeMac()
    session = connect_to(dropping)
    reconnects = []

    def reconnect(picture):
        reconnects.append(picture)
        return connect_to(following, picture=picture)

    viewer = Viewer(session, reconnect=reconnect)
    viewer.start()
    assert wait_for(lambda: viewer.session is not session)
    assert reconnects == [session.picture]
    assert viewer.session.picture is session.picture
    viewer.on_event("key", 0x61, True)
    viewer.stop()
    dropping.join()
    following.join()
    assert viewer.session.closed
    assert following.received[-1] == bytes.fromhex("0401000000000061")


def test_a_session_the_mac_s_user_ended_is_not_followed():
    mac = fakemac.FakeMac(bytes.fromhex("1400000400010001"), hang_up=True)
    session = connect_to(mac)
    reconnects = []
    viewer = Viewer(session, reconnect=reconnects.append)
    viewer.start()
    assert session.wait(10)
    viewer._follower.join(10)
    assert not viewer._follower.is_alive()
    assert reconnects == []
    viewer.stop()
    mac.join()


def test_a_following_session_draws_afresh_on_the_same_picture():
    # The first session drops half way through the recording, its decoders
    # full of what that half left them; the next one plays all of it.
    parser = stream.Parser()
    half = len(FIXTURE) // 2
    parser.feed(FIXTURE[:half])
    dropping = fakemac.FakeMac(FIXTURE[: half - parser.pending], hang_up=True)
    following = fakemac.FakeMac(FIXTURE)
    seen = 0
    done = threading.Event()

    def count(_update):
        nonlocal seen
        seen += 1
        if seen == UPDATES:
            done.set()

    session = connect_to(dropping)
    viewer = Viewer(
        session,
        reconnect=lambda picture: connect_to(
            following, picture=picture, on_update=count
        ),
    )
    viewer.start()
    assert done.wait(30), f"{seen} of {UPDATES} updates"
    snapshot = viewer.session.picture.snapshot()
    viewer.stop()
    dropping.join()
    following.join()
    assert hashlib.sha256(snapshot).hexdigest() == CHECKPOINTS[196]
