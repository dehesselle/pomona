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
from pomona.layout import Layout
from pomona.session import Session

log = logging.getLogger(__name__)

# Seconds between attempts to reconnect; the last repeats.
RECONNECT_DELAYS = (1, 2, 5, 10, 20, 30)
# Seconds a resized window must keep its size before the Mac is asked for it.
RESIZE_SETTLE = 0.3


def run_window(
    session: Session,
    *,
    title: str | None = None,
    ctrl_as_command: bool = False,
    pasteboard: bool = True,
    reconnect: Callable[[Picture], Session] | None = None,
    scale: float | None = None,
    size: tuple[int, int] | None = None,
) -> Session:
    """Show the session's screen in a window until the window is closed or
    the session ends, then close the session.

    The Mac sends its screen at the window's size, pixel for pixel: it scales
    the screen down before sending, and again after the window is resized.
    The window keeps the screen's shape and grows no larger than the Mac can
    send. It opens at `scale` times the Mac's screen, or as large as fits in
    `size` (width, height), or by default at the size that shows the Mac's
    text as large as the client's own; in any case at most nine tenths of the
    client's screen.

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
    viewer = Viewer(
        session, reconnect=reconnect, pasteboard=pasteboard, scale=scale, size=size
    )
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
    """A window's sessions: the one shown, and those following it; and the
    scale the window asks of them."""

    def __init__(
        self,
        session: Session,
        *,
        reconnect: Callable[[Picture], Session] | None = None,
        pasteboard: bool = True,
        scale: float | None = None,
        size: tuple[int, int] | None = None,
        settle: float = RESIZE_SETTLE,
    ) -> None:
        if scale is not None and not 0 < scale <= 1:
            raise ValueError(f"a scale of {scale}, outside (0, 1]")
        self.session = session
        self.picture = session.picture
        self._reconnect = reconnect
        self._pasteboard = pasteboard
        self._first_scale = scale
        self._first_size = size
        self._settle = settle
        self._lock = threading.Lock()
        # The client's pixels per point, the window's inner size in pixels,
        # and the Mac's screen at scale 1, once each is known; and the screen
        # the window was last shaped for.
        self._client_scale: float | None = None
        self._window: tuple[int, int] | None = None
        self._native: tuple[int, int] | None = None
        self._shaped: tuple[int, int] | None = None
        self._timer: threading.Timer | None = None
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
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
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
        elif kind == "size":
            width, height = args
            with self._lock:
                self._window = (int(width), int(height))
            self._fit_later()
        elif kind == "opened":
            adapter, scale, width, height = args
            log.info("window open, drawn by %s, at scale %g", adapter, scale)
            with self._lock:
                self._client_scale = float(scale)
                self._window = (int(width), int(height))
            self._shape()
        elif kind == "scale":
            with self._lock:
                self._client_scale = float(args[0])

    def _attach(self, session: Session) -> None:
        if self._pasteboard:
            session.on_pasteboard = self.picture.set_pasteboard
            if session.pasteboard is not None:
                self.picture.set_pasteboard(session.pasteboard)
        session.on_layout = lambda layout: self._on_layout(session, layout)

    def _on_layout(self, session: Session, layout: Layout | None = None) -> None:
        """A layout came, on the reader thread. A Mac whose screen changed
        reshapes the window. A picture a pixel or two off the window's size,
        from the Mac's rounding, has the window snap to it, so it is shown
        pixel for pixel. Otherwise the window's size is asked for again, as a
        session that just followed another must be asked."""
        with self._lock:
            self._native = session.native_size
            reshape = self._native != self._shaped
            picture = (layout.width, layout.height) if layout else None
            snap = (
                not reshape
                and picture is not None
                and self._window is not None
                and picture != self._window
                and all(abs(a - b) <= 2 for a, b in zip(picture, self._window))
            )
        if reshape:
            self._shape()
        elif snap and picture is not None:
            self.picture.fit_window(*self._native, *picture)
        else:
            self._fit_later()

    def _shape(self) -> None:
        """Give the window the Mac's shape, the most it can send, and a size:
        the one asked for when it first opens, its own area later."""
        with self._lock:
            if self._window is None or self._native is None:
                return
            width, height = self._native
            if self._shaped is None:
                factor = self._opening_scale(width, height)
            else:
                factor = min(1.0, self._window[0] / width, self._window[1] / height)
            self._shaped = self._native
        self.picture.fit_window(
            width, height, round(width * factor), round(height * factor)
        )
        self._fit_later()

    def _opening_scale(self, width: int, height: int) -> float:
        if self._first_size is not None:
            return min(1.0, self._first_size[0] / width, self._first_size[1] / height)
        if self._first_scale is not None:
            return self._first_scale
        density = self.session.density
        if density and self._client_scale:
            # The Mac's text as large as the client's own, as Apple's viewer
            # shows it.
            return min(1.0, self._client_scale / density)
        return 1.0

    def _fit_later(self) -> None:
        """Ask for the window's size once it has settled: every new scale
        brings a layout and a whole screen."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            if self._closing.is_set():
                return
            self._timer = threading.Timer(self._settle, self._fit)
            self._timer.daemon = True
            self._timer.start()

    def _fit(self) -> None:
        with self._lock:
            if self._window is None or self._native is None:
                return
            (width, height), (native_w, native_h) = self._window, self._native
        self.session.request_scale(min(1.0, width / native_w, height / native_h))

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
