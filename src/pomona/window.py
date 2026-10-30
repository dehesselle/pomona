# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Showing a session in a window."""

import logging
from collections.abc import Callable

from pomona import _native
from pomona.session import Session

log = logging.getLogger(__name__)


def run_window(
    session: Session, *, title: str | None = None, ctrl_as_command: bool = False
) -> None:
    """Show the session's screen in a window until the window is closed or
    the session ends, then close the session.

    Keys, clicks and scrolling in the window go to the Mac: Alt as Option,
    the Windows key as Command, or with `ctrl_as_command` Control as Command
    and the Windows key as Control. On Windows the window keeps the Windows
    keys, Alt-Tab and the like to itself while it is in front.

    Runs on the calling thread, which on macOS must be the main thread, and a
    process can open one window in its lifetime. The session's own threads go
    on meanwhile. Raises RuntimeError when no window or graphics adapter can be
    had.
    """
    try:
        _native.run_window(
            session.picture,
            events(session),
            title or session.server.name,
            ctrl_as_command,
        )
    finally:
        session.close()


def events(session: Session) -> Callable[..., None]:
    """What the window tells, passed on to `session`."""

    def on_event(kind: str, *args: object) -> None:
        if kind == "pointer":
            session.pointer(*args)
        elif kind == "key":
            session.key(*args)
        elif kind == "scroll":
            session.scroll(*args)
        elif kind == "opened":
            adapter, scale = args
            log.info("window open, drawn by %s, at scale %g", adapter, scale)
            session.set_client_scale(float(scale))
        elif kind == "scale":
            session.set_client_scale(float(args[0]))

    return on_event
