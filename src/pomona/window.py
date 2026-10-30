# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Showing a session in a window."""

import logging

from pomona import _native
from pomona.session import Session

log = logging.getLogger(__name__)


def run_window(session: Session, *, title: str | None = None) -> None:
    """Show the session's screen in a window until the window is closed or
    the session ends, then close the session.

    Runs on the calling thread, which on macOS must be the main thread, and a
    process can open one window in its lifetime. The session's own threads go
    on meanwhile. Raises RuntimeError when no window or graphics adapter can be
    had.
    """

    def on_event(kind: str, *args: object) -> None:
        if kind == "opened":
            adapter, scale = args
            log.info("window open, drawn by %s, at scale %g", adapter, scale)
            session.set_client_scale(float(scale))
        elif kind == "scale":
            session.set_client_scale(float(args[0]))

    try:
        _native.run_window(session.picture, on_event, title or session.server.name)
    finally:
        session.close()
