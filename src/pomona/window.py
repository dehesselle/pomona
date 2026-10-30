# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Showing a session in a window."""

import logging
import threading
from collections.abc import Callable

from pomona import _native
from pomona._native import Picture
from pomona.errors import PomonaError
from pomona.session import Session

log = logging.getLogger(__name__)

# Seconds between attempts to reconnect; the last repeats.
RECONNECT_DELAYS = (1, 2, 5, 10, 20, 30)


def run_window(
    session: Session,
    *,
    title: str | None = None,
    ctrl_as_command: bool = False,
    pasteboard: bool = True,
    reconnect: Callable[[Picture], Session] | None = None,
) -> Session:
    """Show the session's screen in a window until the window is closed or
    the session ends, then close the session.

    Keys, clicks and scrolling in the window go to the Mac: Alt as Option,
    the Windows key as Command, or with `ctrl_as_command` Control as Command
    and the Windows key as Control. On Windows the window keeps the Windows
    keys, Alt-Tab and the like to itself while it is in front. With
    `pasteboard`, text copied on the Mac lands on the local pasteboard, and
    text copied locally goes to the Mac when the window gets the focus.

    With `reconnect`, a session the connection dropped is followed by
    another: `reconnect(picture)` signs in again and returns a session drawing
    on `picture`, the window's, as `connect(..., picture=picture)` does. It is
    tried until it succeeds, the Mac refuses the sign-in, or the window is
    closed. A session the Mac's user ended is not followed.

    Returns the last session, which is `session` unless it was followed.
    Runs on the calling thread, which on macOS must be the main thread, and a
    process can open one window in its lifetime. The session's own threads go
    on meanwhile. Raises RuntimeError when no window or graphics adapter can be
    had.
    """
    viewer = Viewer(session, reconnect=reconnect, pasteboard=pasteboard)
    viewer.start()
    try:
        _native.run_window(
            session.picture,
            viewer.on_event,
            title or session.server.name,
            ctrl_as_command,
            pasteboard,
        )
    finally:
        viewer.stop()
    return viewer.session


def worth_reconnecting(error: BaseException | None) -> bool:
    """Whether a session that ended with `error` should be followed: when the
    connection failed or the Mac hung up, not when the Mac's user ended the
    session, refused the sign-in, or sent what Pomona cannot read."""
    return isinstance(error, OSError) or type(error) is PomonaError


class Viewer:
    """A window's sessions: the one shown, and those following it."""

    def __init__(
        self,
        session: Session,
        *,
        reconnect: Callable[[Picture], Session] | None = None,
        pasteboard: bool = True,
    ) -> None:
        self.session = session
        self.picture = session.picture
        self._reconnect = reconnect
        self._pasteboard = pasteboard
        self._scale: float | None = None
        self._closing = threading.Event()
        self._follower = threading.Thread(
            target=self._follow, name="pomona-window", daemon=True
        )
        self._attach(session)

    def start(self) -> None:
        self._follower.start()

    def stop(self) -> None:
        """The window closed: end the session, and any attempt to follow it."""
        self._closing.set()
        self.session.close()
        self._follower.join(timeout=5)

    def on_event(self, kind: str, *args: object) -> None:
        """What the window tells, passed on to the session shown."""
        session = self.session
        if kind == "pointer":
            session.pointer(*args)
        elif kind == "key":
            session.key(*args)
        elif kind == "scroll":
            session.scroll(*args)
        elif kind == "pasteboard":
            session.set_pasteboard(*args)
        elif kind == "opened":
            adapter, scale = args
            log.info("window open, drawn by %s, at scale %g", adapter, scale)
            self._scale = float(scale)
            session.set_client_scale(self._scale)
        elif kind == "scale":
            self._scale = float(args[0])
            session.set_client_scale(self._scale)

    def _attach(self, session: Session) -> None:
        if self._pasteboard:
            session.on_pasteboard = self.picture.set_pasteboard
            if session.pasteboard is not None:
                self.picture.set_pasteboard(session.pasteboard)
        if self._scale is not None:
            session.set_client_scale(self._scale)

    def _follow(self) -> None:
        """Wait for each session to end, follow it with another where that is
        wanted, and close the window when none follows."""
        while True:
            self.session.wait()
            if self._closing.is_set():
                return
            error = self.session.error
            following = None
            if self._reconnect is not None and worth_reconnecting(error):
                log.warning("the session ended: %s; reconnecting", error)
                following = self._next(self._reconnect)
            if following is None:
                self.picture.quit()
                return
            self.session = following

    def _next(self, reconnect: Callable[[Picture], Session]) -> Session | None:
        delays = iter(RECONNECT_DELAYS)
        delay = next(delays)
        while not self._closing.is_set():
            try:
                session = reconnect(self.picture)
            except (OSError, PomonaError) as failure:
                if not worth_reconnecting(failure):
                    log.warning("could not reconnect: %s", failure)
                    return None
                log.warning("could not reconnect: %s; again in %d s", failure, delay)
                self._closing.wait(delay)
                delay = next(delays, delay)
                continue
            if self._closing.is_set():
                session.close()
                return None
            log.warning("reconnected")
            self._attach(session)
            return session
        return None
