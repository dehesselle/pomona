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
"""

import logging
import queue
import socket
import threading
from collections.abc import Callable
from typing import Self

from pomona import connection, layout, messages, stream
from pomona._native import Picture
from pomona.errors import PomonaError, ProtocolError
from pomona.record import RecordReader, RecordWriter

log = logging.getLogger(__name__)

# Buttons for `Session.pointer`, as RFB numbers them.
LEFT = 0x01
MIDDLE = 0x02
RIGHT = 0x04

QUALITIES = {"adaptive": messages.ADAPTIVE_ENCODINGS}

_PICTURE_ENCODINGS = frozenset({stream.ADAPTIVE, stream.ZLIB, stream.ZRLE, stream.RAW})
_WHEEL_UP = 0x08
_WHEEL_DOWN = 0x10


class Session:
    """One Mac's screen. Made by `connect`; close it, or use it as a context
    manager.

    `picture` is the framebuffer, `layout` the Mac's last display layout.
    `on_update`, if set, is called on the reader thread with each
    `stream.FramebufferUpdate` once it is drawn; keep it short.
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
    ) -> None:
        self.server = server
        self.picture = Picture(server.width, server.height)
        self.layout: layout.Layout | None = None
        self.on_update = on_update
        self.bytes_received = 0
        # Why the session ended, if it did not end by `close`.
        self.error: BaseException | None = None
        self._sock = sock
        self._push_interval_us = push_interval_us
        self._records_in = RecordReader(*keys)
        self._records_out = RecordWriter(*keys)
        self._parser = stream.Parser()
        self._outbox: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._closed = threading.Event()
        self._closing = threading.Lock()

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
                self.error = PomonaError("the Mac closed the connection")
        except OSError as error:
            # Closing the socket is how `close` stops this thread.
            if not self._closed.is_set():
                self.error = error
        # Whatever stops the reader, the caller's on_update included, ends the
        # session, and the caller finds out why here rather than on stderr.
        except Exception as error:  # noqa: BLE001
            self.error = error
        finally:
            self._shut_down()

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
        if not isinstance(message, stream.FramebufferUpdate):
            # MiscStatus, Bell and the pasteboard: later.
            return
        rearm = False
        for rect in message.rects:
            if rect.encoding in _PICTURE_ENCODINGS:
                try:
                    self.picture.decode(
                        rect.encoding, rect.x, rect.y, rect.w, rect.h, rect.body
                    )
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
) -> Session:
    """Sign in to a Mac's Screen Sharing and start a Standard session.

    `push_interval_us` paces the Mac's pushed updates: 0, as Apple's viewer
    asks, is as fast as the Mac captures; 33,333 is about 30 a second.
    Raises AuthenticationError for a refused sign-in, ProtocolError for a
    server that does not speak as a Mac, and OSError for the connection.
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
        )
    except BaseException:
        sock.close()
        raise
