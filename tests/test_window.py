# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

from pomona.window import events


class Recorder:
    """Stands in for a session and keeps what it was asked."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


def test_the_window_input_reaches_the_session():
    session = Recorder()
    on_event = events(session)
    on_event("opened", "WARP", 1.5)
    on_event("key", 0xFFE7, True)
    on_event("pointer", 10, 20, 1)
    on_event("scroll", 0, 40, 10, 20)
    on_event("scale", 2.0)
    on_event("something new", 1)
    assert session.calls == [
        ("set_client_scale", 1.5),
        ("key", 0xFFE7, True),
        ("pointer", 10, 20, 1),
        ("scroll", 0, 40, 10, 20),
        ("set_client_scale", 2.0),
    ]
