# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""A signed-in session in Standard mode: the picture in, input out.

Two threads run it. The reader takes bytes off the socket, through the record
layer and the stream parser, and decodes rectangles onto the picture; it never
waits on anyone. The writer sends everything queued for the Mac, in order,
through the record layer's other direction. The Mac reads nothing while it
writes an update, so a caller sending input must never block on the socket,
and with the writer it never does.

Standard mode is armed as Apple's viewer arms it: once the first display layout
arrives, the Mac pushes every change unasked (AutoFrameBufferUpdate), and it
is armed again after every layout, which drops the arming.

The Mac says when its pasteboard changes (MiscStatus 2); the session then
fetches it, and hands its text to `on_pasteboard`.
"""

import itertools
import logging
import queue
import socket
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Self

from pomona import connection, layout, messages, pasteboard, stream
from pomona._native import Picture, StreamError
from pomona.cursor import Cursor, CursorCache
from pomona.errors import EndedAtMacError, PomonaError, ProtocolError
from pomona.record import RecordReader, RecordWriter

log = logging.getLogger(__name__)

# Buttons for `Session.pointer`, as RFB numbers them.
LEFT = 0x01
MIDDLE = 0x02
RIGHT = 0x04

QUALITIES = {"adaptive": messages.ADAPTIVE_ENCODINGS, "full": messages.FULL_ENCODINGS}

_PICTURE_ENCODINGS = frozenset({stream.ADAPTIVE, stream.ZLIB, stream.ZRLE, stream.RAW})
# Codecs whose one deflate stream may be lost with a rectangle that fails.
_STREAM_ENCODINGS = frozenset({stream.ZLIB, stream.ZRLE})
_WHEEL_UP = 0x08
_WHEEL_DOWN = 0x10
# MiscStatus commands acted on.
_USER_ENDED = 1
_PASTEBOARD_CHANGED = 2
_USER_SESSION_CHANGED = 17
_KEYBOARD_SOURCE = struct.Struct(">HIH")
# Scale factors: the smallest asked for, and the least change worth asking.
_MIN_SCALE = 0.05
_SCALE_STEP = 0.001


@dataclass(frozen=True, slots=True)
class KeyboardSource:
    """The Mac's keyboard layout, such as `com.apple.keylayout.ABC`, and
    whether a secure text field, such as a password prompt, has the focus."""

    layout: str
    secure: bool


class Session:
    """One Mac's screen. Made by `connect`; close it, or use it as a context
    manager.

    `picture` is the framebuffer, `layout` the Mac's last display layout,
    `cursor` the pointer's shape and `cursor_position` where the Mac last put
    it. `pasteboard` is the text last on the Mac's pasteboard, as far as the
    session knows, and `keyboard_source` the Mac's keyboard layout.

    `on_update`, if set, is called on the reader thread with each
    `stream.FramebufferUpdate` once it is drawn, and `on_pasteboard` with the
    text of the Mac's pasteboard each time it changes; keep them short.
    """

    def __init__(
        self,
        sock: socket.socket,
        server: connection.ServerInit,
        keys: tuple[bytes, bytes],
        *,
        quality: str,
        push_interval_us: int,
        on_update: Callable[[stream.FramebufferUpdate], None] | None,
        on_pasteboard: Callable[[str], None] | None = None,
        picture: Picture | None = None,
    ) -> None:
        self.server = server
        if picture is None:
            picture = Picture(server.width, server.height)
        else:
            # What another session's stream left in the decoders would
            # garble this one's.
            picture.restart()
            if (picture.width, picture.height) != (server.width, server.height):
                picture.resize(server.width, server.height)
        self.picture = picture
        self.layout: layout.Layout | None = None
        self.cursor: Cursor | None = None
        self.cursor_position: tuple[int, int] | None = None
        self.pasteboard: str | None = None
        self.keyboard_source: KeyboardSource | None = None
        self.on_update = on_update
        self.on_pasteboard = on_pasteboard
        self.on_layout: Callable[[layout.Layout], None] | None = None
        self.bytes_received = 0
        # Why the session ended, if it did not end by `close`.
        self.error: BaseException | None = None
        self._sock = sock
        self._push_interval_us = push_interval_us
        self._records_in = RecordReader(*keys)
        self._records_out = RecordWriter(*keys)
        self._parser = stream.Parser()
        self._cursors = CursorCache()
        # The client's pixels per point, once a window says, and the scale
        # asked of the Mac and not yet confirmed by a layout.
        self._client_scale: float | None = None
        self._scale_asked: float | None = None
        self._outbox: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._closed = threading.Event()
        self._closing = threading.Lock()
        self._fetches = itertools.count(1)
        self._user_ended = False

        # The pixel format and encodings go first, inside the record layer: a
        # list sent in cleartext got no Adaptive picture at all.
        self._send(messages.set_pixel_format())
        self._send(messages.set_encodings(QUALITIES[quality]))
        self._threads = (
            threading.Thread(target=self._read, name="pomona-reader", daemon=True),
            threading.Thread(target=self._write, name="pomona-writer", daemon=True),
        )
        for thread in self._threads:
            thread.start()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the session to end; return whether it has."""
        return self._closed.wait(timeout)

    def close(self) -> None:
        """End the session, after sending what was already queued."""
        with self._closing:
            ending = not self._closed.is_set()
            self._closed.set()
        if ending:
            self._outbox.put(None)
        reader, writer = self._threads
        if writer is not threading.current_thread():
            # The writer stops at the end of the queue; a Mac that reads
            # nothing gets cut off below instead.
            writer.join(timeout=5)
        self._hang_up()
        if reader is not threading.current_thread():
            reader.join()
        self._sock.close()

    # Input. Positions are framebuffer pixels; anything sent after the
    # session ended is dropped.

    def key(self, keysym: int, down: bool) -> None:
        """Press or release the key of an X11 keysym."""
        self._send(messages.key_event(keysym, down))

    def pointer(self, x: int, y: int, buttons: int = 0) -> None:
        """Move the pointer, with `buttons` (LEFT, MIDDLE, RIGHT) held."""
        # On 003.889 the Mac reads the mask as macOS button numbers: right and
        # middle swap places.
        mask = (
            (buttons & LEFT)
            | (0x02 if buttons & RIGHT else 0)
            | (0x04 if buttons & MIDDLE else 0)
        )
        self._send(messages.pointer_event(mask, *self._unscaled(x, y)))

    def scroll(self, dx: int, dy: int, x: int, y: int) -> None:
        """Scroll by `dx`, `dy` points (positive right and down) at `x`, `y`."""
        x, y = self._unscaled(x, y)
        if self.server.accepts(messages.EVENT_2):
            self._send(messages.scroll_wheel(dx, dy, x, y))
        elif dy:
            # A Mac without the scroll message takes a wheel pulse, alone.
            pulse = _WHEEL_DOWN if dy > 0 else _WHEEL_UP
            self._send(messages.pointer_event(pulse, x, y))
            self._send(messages.pointer_event(0, x, y))

    def set_pasteboard(self, text: str) -> None:
        """Put `text` on the Mac's pasteboard; empty text clears it."""
        message = messages.set_pasteboard(text)
        if len(message) > stream.MAX_PASTEBOARD:
            # The Mac would close the connection.
            log.warning("%d bytes of text are too many for the Mac", len(text))
            return
        self.pasteboard = text
        self._send(message)

    @property
    def native_size(self) -> tuple[int, int]:
        """The Mac's screen in pixels before the Mac scales it: the most it
        can send."""
        if self.layout is None:
            return self.server.width, self.server.height
        scale = self.layout.server_scale
        return round(self.layout.width / scale), round(self.layout.height / scale)

    @property
    def density(self) -> float | None:
        """The Mac's pixels per point on the screen shown, once a layout says."""
        if self.layout is None or not self.layout.displays:
            return None
        shown = [
            d
            for d in self.layout.displays
            if self.layout.current is None or d.id == self.layout.current
        ] or list(self.layout.displays)
        density = max(d.density for d in shown)
        return density if density > 0 else None

    def request_scale(self, factor: float) -> None:
        """Ask the Mac to send its screen scaled by `factor`, in (0, 1]: the
        Mac shrinks it before encoding, and its own screen stays as it is."""
        factor = min(1.0, max(factor, _MIN_SCALE))
        current = self.layout.server_scale if self.layout else 1.0
        if abs(factor - current) < _SCALE_STEP:
            self._scale_asked = None
        elif (
            self._scale_asked is None or abs(factor - self._scale_asked) >= _SCALE_STEP
        ):
            log.info("asking the Mac to scale its screen by %.4f", factor)
            self._scale_asked = factor
            self._send(messages.set_server_scaling(factor))

    def set_client_scale(self, scale: float) -> None:
        """Tell the session the client's pixels per point, so that a Retina
        Mac sends its screen at the client's density rather than its own,
        `min(1, client density / Mac density)` as Apple's viewer asks. A window
        asks for its own size instead (`request_scale`)."""
        self._client_scale = scale
        self._rescale()

    def _rescale(self) -> None:
        density = self.density
        if self._client_scale is not None and density is not None:
            self.request_scale(self._client_scale / density)

    def _unscaled(self, x: int, y: int) -> tuple[int, int]:
        """The Mac reads positions in its unscaled framebuffer's pixels."""
        scale = self.layout.server_scale if self.layout else 1.0
        return round(x / scale), round(y / scale)

    def _send(self, message: bytes) -> None:
        if not self._closed.is_set():
            self._outbox.put(message)

    def _read(self) -> None:
        try:
            while data := self._sock.recv(1 << 20):
                self.bytes_received += len(data)
                for message in self._parser.feed(self._records_in.feed(data)):
                    self._handle(message)
            if not self._closed.is_set():
                self.error = self._ended(None)
        except OSError as error:
            # Closing the socket is how `close` stops this thread.
            if not self._closed.is_set():
                self.error = self._ended(error)
        # Whatever stops the reader, the caller's on_update included, ends the
        # session, and the caller finds out why here rather than on stderr.
        except Exception as error:  # noqa: BLE001
            self.error = error
        finally:
            self._shut_down()

    def _ended(self, error: OSError | None) -> BaseException:
        """Why the Mac's end of the connection went away."""
        if self._user_ended:
            return EndedAtMacError("the Mac's user ended the session")
        return error or PomonaError("the Mac closed the connection")

    def _write(self) -> None:
        try:
            while (message := self._outbox.get()) is not None:
                self._sock.sendall(self._records_out.frame(message))
        except OSError as error:
            if not self._closed.is_set():
                self.error = error
        finally:
            self._shut_down()

    def _handle(self, message: stream.Message) -> None:
        if isinstance(message, stream.MiscStatus):
            self._misc_status(message.command)
            return
        if isinstance(message, stream.PasteboardContents):
            self._apply_pasteboard(message)
            return
        if not isinstance(message, stream.FramebufferUpdate):
            # A bell, or an RFB cut text the Mac does not send.
            return
        rearm = False
        for rect in message.rects:
            if rect.encoding in _PICTURE_ENCODINGS:
                try:
                    self.picture.decode(
                        rect.encoding, rect.x, rect.y, rect.w, rect.h, rect.body
                    )
                except StreamError as error:
                    # zlib and ZRLE inflate each rectangle on the window the
                    # ones before it left: once a piece is lost, so is the rest.
                    raise ProtocolError(str(error)) from None
                except ValueError as error:
                    # The framing is intact, so the session goes on and the
                    # next update paints over what this one missed.
                    log.warning(
                        "a %dx%d rectangle at %d,%d did not decode: %s",
                        rect.w,
                        rect.h,
                        rect.x,
                        rect.y,
                        error,
                    )
            elif rect.encoding == stream.DISPLAY_LAYOUT:
                self._apply_layout(layout.parse(rect.body))
                rearm = True
            elif rect.encoding == stream.CURSOR_IMAGE:
                self._apply_cursor(rect)
            elif rect.encoding == stream.CURSOR_POS:
                self.cursor_position = (rect.x, rect.y)
            elif rect.encoding == stream.KEYBOARD_SOURCE:
                self._apply_keyboard_source(rect.body)
            elif rect.encoding == stream.REKEY:
                # Records already framed under the old key would fail the
                # Mac's check, so a rekey cannot be followed.
                raise ProtocolError("the Mac re-keyed in the middle of the session")
        # Asked once the update is through, so the request is not answered
        # with the update still being read.
        if rearm:
            self._arm()
        if self.on_update is not None:
            self.on_update(message)

    def _misc_status(self, command: int | None) -> None:
        if command == _PASTEBOARD_CHANGED:
            self._send(messages.fetch_pasteboard(next(self._fetches)))
        elif command == _USER_ENDED:
            # The Mac closes the connection next.
            log.info("the Mac's user ended the session")
            self._user_ended = True
        elif command == _USER_SESSION_CHANGED:
            # A login moves the session to the new user's screen, which comes
            # as a layout, or the Mac closes the connection.
            log.info("a user logged in or out at the Mac")

    def _apply_pasteboard(self, contents: stream.PasteboardContents) -> None:
        try:
            text = pasteboard.text(contents.compressed, contents.uncompressed)
        except ProtocolError as error:
            # Each pasteboard is a zlib stream of its own: nothing after it
            # depends on this one.
            log.warning("%s", error)
            return
        if text is None:
            log.info("the Mac's pasteboard holds no text")
            return
        self.pasteboard = text
        if self.on_pasteboard is not None:
            self.on_pasteboard(text)

    def _apply_keyboard_source(self, body: bytes) -> None:
        # u16 version, u32 secure flag, u16 length, the layout's name
        if len(body) < _KEYBOARD_SOURCE.size:
            return
        _, secure, length = _KEYBOARD_SOURCE.unpack_from(body)
        name = body[_KEYBOARD_SOURCE.size : _KEYBOARD_SOURCE.size + length]
        source = KeyboardSource(name.decode(errors="replace"), bool(secure & 1))
        if source != self.keyboard_source:
            log.info(
                "the Mac's keyboard: %s%s",
                source.layout,
                ", in a secure text field" if source.secure else "",
            )
            self.keyboard_source = source

    def _apply_cursor(self, rect: stream.Rect) -> None:
        try:
            shape = self._cursors.accept(rect.x, rect.y, rect.w, rect.h, rect.body)
        except ProtocolError as error:
            # Each shape is a zlib stream of its own: the pointer keeps the
            # shape it had, and nothing after this one depends on it.
            log.warning("%s", error)
            return
        if shape is not None:
            self.cursor = shape
            self.picture.set_cursor(
                shape.width, shape.height, *shape.hotspot, shape.rgba
            )

    def _apply_layout(self, new: layout.Layout) -> None:
        log.info(
            "display layout: %dx%d, %d display(s), server scale %g",
            new.width,
            new.height,
            len(new.displays),
            new.server_scale,
        )
        if (new.width, new.height) != (self.picture.width, self.picture.height):
            self.picture.resize(new.width, new.height)
        self.layout = new
        if (
            self._scale_asked is not None
            and abs(new.server_scale - self._scale_asked) < _SCALE_STEP
        ):
            self._scale_asked = None
        self._rescale()
        if self.on_layout is not None:
            self.on_layout(new)

    def _arm(self) -> None:
        width, height = self.picture.width, self.picture.height
        self._send(
            messages.auto_framebuffer_update(self._push_interval_us, width, height)
        )
        self._send(messages.framebuffer_update_request(False, 0, 0, width, height))

    def _shut_down(self) -> None:
        """End the session from one of its threads: the connection failed or
        the Mac hung up."""
        with self._closing:
            if self._closed.is_set():
                return
            self._closed.set()
        self._outbox.put(None)
        self._hang_up()

    def _hang_up(self) -> None:
        # Wakes the reader out of recv, and the writer out of sendall.
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def connect(
    host: str,
    port: int = 5900,
    *,
    user: str,
    password: str,
    quality: str = "adaptive",
    push_interval_us: int = 0,
    timeout: float = 15.0,
    on_update: Callable[[stream.FramebufferUpdate], None] | None = None,
    on_pasteboard: Callable[[str], None] | None = None,
    picture: Picture | None = None,
) -> Session:
    """Sign in to a Mac's Screen Sharing and start a Standard session.

    `push_interval_us` paces the Mac's pushed updates: 0, as Apple's viewer
    asks, is as fast as the Mac captures; 33,333 is about 30 a second.
    `picture` draws the session on an existing picture, a window's when
    reconnecting. Raises AuthenticationError for a refused sign-in,
    ProtocolError for a server that does not speak as a Mac, and OSError for
    the connection.
    """
    if quality not in QUALITIES:
        raise ValueError(f"unknown quality {quality!r}; known: {', '.join(QUALITIES)}")
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        wrap_key, server = connection.sign_in(sock, user, password)
        keys = connection.start_records(sock, wrap_key)
        sock.settimeout(None)
        return Session(
            sock,
            server,
            keys,
            quality=quality,
            push_interval_us=push_interval_us,
            on_update=on_update,
            on_pasteboard=on_pasteboard,
            picture=picture,
        )
    except BaseException:
        sock.close()
        raise
