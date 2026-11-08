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
    on_event("opened", "WARP", 1.5, 800, 600)
    on_event("key", 0xFFE7, True)
    on_event("pointer", 10, 20, 1)
    on_event("scroll", 0, 40, 10, 20)
    on_event("pasteboard", "copied")
    on_event("something new", 1)
    assert session.calls == [
        ("key", 0xFFE7, True),
        ("pointer", 10, 20, 1),
        ("scroll", 0, 40, 10, 20),
        ("set_pasteboard", "copied"),
    ]


class Shown:
    """A picture whose window keeps what it is told to take."""

    def __init__(self):
        self.fits = []

    def fit_window(self, *fit):
        self.fits.append(fit)

    def set_pasteboard(self, text):
        pass


class FourK:
    """A session of a Retina Mac with a 4K screen, 1920x1080 in points."""

    native_size = (3840, 2160)
    density = 2.0
    pasteboard = None

    def __init__(self):
        self.picture = Shown()
        self.scales = []

    def request_scale(self, factor):
        self.scales.append(round(factor, 4))


def shown(viewer, session):
    """Open the window, and have the Mac's first layout arrive."""
    viewer.on_event("opened", "GPU", 1.75, 1200, 800)
    viewer._on_layout(session)


def test_the_window_opens_at_the_client_s_density_and_the_mac_follows_it():
    session = FourK()
    viewer = Viewer(session, settle=0)
    shown(viewer, session)
    # 1.75 pixels per point here, 2 there: the Mac's text as large as ours.
    assert session.picture.fits == [(3840, 2160, 3360, 1890)]
    # The window resizes to that, or what the screen has room for, and the
    # Mac is asked for what the window got.
    viewer.on_event("size", 2400, 1350)
    assert wait_for(lambda: session.scales[-1:] == [0.625])
    viewer.on_event("size", 1920, 1080)
    assert wait_for(lambda: session.scales[-1:] == [0.5])


def test_a_size_or_a_scale_asked_for_decides_the_opening_size():
    session = FourK()
    shown(Viewer(session, size=(1600, 1200), settle=0), session)
    session_too = FourK()
    shown(Viewer(session_too, scale=0.25, settle=0), session_too)
    assert session.picture.fits == [(3840, 2160, 1600, 900)]
    assert session_too.picture.fits == [(3840, 2160, 960, 540)]


def test_a_resize_is_asked_for_once_it_settles():
    session = FourK()
    viewer = Viewer(session, settle=0.2)
    shown(viewer, session)
    for width in range(1000, 2000, 100):
        viewer.on_event("size", width, width * 9 // 16)
    assert wait_for(lambda: session.scales)
    threading.Event().wait(0.4)
    # The last size only; its height, rounded down, is the tighter fit.
    assert session.scales == [round(min(1900 / 3840, 1068 / 2160), 4)]


def test_the_window_snaps_to_a_picture_the_mac_rounded():
    from types import SimpleNamespace

    session = FourK()
    viewer = Viewer(session, settle=0)
    shown(viewer, session)
    viewer.on_event("size", 1244, 700)
    viewer._on_layout(session, SimpleNamespace(width=1244, height=699))
    assert session.picture.fits[-1] == (3840, 2160, 1244, 699)
    # A picture further off is the Mac's to change, not the window's.
    viewer._on_layout(session, SimpleNamespace(width=1200, height=675))
    assert session.picture.fits[-1] == (3840, 2160, 1244, 699)


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
    assert done.wait(30), (
        f"{seen} of {UPDATES} updates; the first session"
        f" {'ended: ' + repr(session.error) if session.wait(0) else 'never ended'}"
    )
    snapshot = viewer.session.picture.snapshot()
    viewer.stop()
    dropping.join()
    following.join()
    assert hashlib.sha256(snapshot).hexdigest() == CHECKPOINTS[196]
