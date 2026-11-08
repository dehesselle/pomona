# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""A stand-in for a Mac's Screen Sharing, for tests that need no Mac.

It speaks the server's side of the handshake (type-30 sign-in, ServerInit,
the cleartext prelude and the rekey) and then the record layer, and replays a
recorded RFB stream to the client. Its record layer is written apart from
`pomona.record`, so that a mistake made the same way on both sides cannot
pass unnoticed.
"""

import hashlib
import secrets
import socket
import struct
import threading
import zlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from pomona import messages

# Any modulus will do for the arithmetic; a Mersenne prime keeps it honest.
# 2**521 - 1 is 66 bytes long, inside the 64..1024 a client takes.
PRIME = 2**521 - 1
KEY_BYTES = 66
GENERATOR = 5
WIDTH, HEIGHT = 1440, 900
# Control privilege, extended ServerInit.
FLAGS = 0x12
# Accepts every client message type; NO_SCROLL lacks the scroll message (0x17).
COMMANDS = bytes([0xFF] * 16)
NO_SCROLL = bytes([0xFF, 0xFF, 0xFE]) + bytes([0xFF] * 13)
# A pasteboard with nothing on it: one item with no flavors.
EMPTY_PASTEBOARD = bytes(4)
PRELUDE = (
    messages.viewer_info()
    + messages.set_mode_control()
    + messages.auto_pasteboard(True)
    + messages.set_encryption_start()
    + messages.set_encryption_decrypt_inbound()
)


def _read(conn: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = conn.recv(count - len(data))
        if not chunk:
            raise EOFError("the client closed the connection")
        data += chunk
    return data


class _Records:
    """One direction of the record layer, the Mac's way round."""

    def __init__(self, key: bytes, iv: bytes, encrypt: bool) -> None:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        self._cipher = cipher.encryptor() if encrypt else cipher.decryptor()
        self._sequence = 0

    def _mac(self, data: bytes) -> bytes:
        mac = hashlib.sha1(self._sequence.to_bytes(4, "big") + data).digest()
        self._sequence += 1
        return mac

    def seal(self, body: bytes) -> bytes:
        plain = len(body).to_bytes(2, "big") + body
        plain += b"\0" * ((16 - (len(plain) + 20) % 16) % 16)
        plain += self._mac(plain)
        return len(plain).to_bytes(2, "big") + self._cipher.update(plain)

    def open(self, conn: socket.socket) -> bytes:
        length = int.from_bytes(_read(conn, 2), "big")
        plain = self._cipher.update(_read(conn, length))
        if plain[-20:] != self._mac(plain[:-20]):
            raise AssertionError("a client record failed its integrity check")
        return plain[2 : 2 + int.from_bytes(plain[:2], "big")]


class FakeMac:
    """Serves one connection on 127.0.0.1, in a thread.

    `stream` is what to replay once the client has set its encodings. It goes
    out in records of the sizes `chunks` cycles through, so messages span
    records as they do from a Mac. What the client sent after the rekey is in
    `received`, and an assertion that failed in the thread is in `failure`.
    With `hang_up`, it ends its side once the stream is out, and reads what
    the client still sends, unanswered, until the client closes too. With
    `abort`, it closes the connection there instead, with what the client sent
    unread: the kernel answers the client with a reset, which the client finds
    when it next sends, but on macOS may never find while it only reads.
    Otherwise it waits for the client until `idle` seconds pass without a
    message, and answers a pasteboard fetch with `pasteboard`, an uncompressed
    archive.
    """

    def __init__(
        self,
        stream: bytes = b"",
        *,
        user: str = "agent",
        password: str = "secret",
        chunks: tuple[int, ...] = (65498, 7, 40000, 1),
        misc_status_before_rekey: bool = True,
        commands: bytes = COMMANDS,
        hang_up: bool = False,
        abort: bool = False,
        idle: float = 5.0,
        pasteboard: bytes = EMPTY_PASTEBOARD,
    ) -> None:
        self.stream = stream
        self.user, self.password = user, password
        self.chunks = chunks
        self.misc_status_before_rekey = misc_status_before_rekey
        self.commands = commands
        self.hang_up = hang_up
        self.abort = abort
        self.idle = idle
        self.pasteboard = pasteboard
        self.received: list[bytes] = []
        self.failure: BaseException | None = None
        self.signed_in = threading.Event()
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def join(self, timeout: float = 10) -> None:
        self._thread.join(timeout)
        assert not self._thread.is_alive(), "the fake Mac is still running"
        if self.failure is not None:
            raise self.failure

    def _serve(self) -> None:
        try:
            conn, _ = self._listener.accept()
            with conn:
                self._session(conn)
        except BaseException as error:  # noqa: BLE001 - handed to the test by join()
            self.failure = error
        finally:
            self._listener.close()

    def _session(self, conn: socket.socket) -> None:
        conn.sendall(b"RFB 003.889\n")
        assert _read(conn, 12) == b"RFB 003.889\n"
        conn.sendall(bytes([4, 30, 33, 36, 35]))
        assert _read(conn, 1) == bytes([30])

        private = secrets.randbelow(PRIME - 2) + 1
        ours = pow(GENERATOR, private, PRIME).to_bytes(KEY_BYTES, "big")
        conn.sendall(
            struct.pack(">HH", GENERATOR, KEY_BYTES)
            + PRIME.to_bytes(KEY_BYTES, "big")
            + ours
        )
        credentials = _read(conn, 128)
        theirs = int.from_bytes(_read(conn, KEY_BYTES), "big")
        shared = pow(theirs, private, PRIME).to_bytes(KEY_BYTES, "big")
        wrap_key = hashlib.md5(shared).digest()
        ecb = Cipher(algorithms.AES(wrap_key), modes.ECB())
        plain = ecb.decryptor().update(credentials)
        user = plain[:64].split(b"\0")[0].decode()
        password = plain[64:].split(b"\0")[0].decode()
        if (user, password) != (self.user, self.password):
            conn.sendall(struct.pack(">I", 1))
            return
        conn.sendall(struct.pack(">I", 0))
        self.signed_in.set()

        assert _read(conn, 1) == bytes([0x81])
        name = struct.pack(">HI", 0, FLAGS) + self.commands + b"Fake Mac"
        conn.sendall(
            struct.pack(">HH", WIDTH, HEIGHT)
            + bytes(16)
            + struct.pack(">I", len(name))
            + name
        )

        assert _read(conn, len(PRELUDE)) == PRELUDE
        if self.misc_status_before_rekey:
            conn.sendall(struct.pack(">BxH", 0x14, 4) + b"\0\0\0\2")
        key, iv = secrets.token_bytes(16), secrets.token_bytes(16)
        wrapped = ecb.encryptor().update(key + iv)
        conn.sendall(
            struct.pack(">BxHHHHHi", 0, 1, 0, 0, 0, 0, 0x44F)
            + struct.pack(">I", 1)
            + wrapped
        )
        inbound = _Records(key, iv, encrypt=False)
        outbound = _Records(key, iv, encrypt=True)

        # The pixel format and the encodings, before anything is sent.
        self.received += [inbound.open(conn), inbound.open(conn)]
        position, turn = 0, 0
        while position < len(self.stream):
            size = self.chunks[turn % len(self.chunks)]
            conn.sendall(outbound.seal(self.stream[position : position + size]))
            position += size
            turn += 1
        if self.abort:
            return
        conn.settimeout(self.idle)
        if self.hang_up:
            self._hang_up(conn)
            return
        # What the client sent meanwhile: arming, a request, input. It stops
        # with the client closing the connection.
        try:
            while True:
                message = inbound.open(conn)
                self.received.append(message)
                if message[0] == 0x0B:
                    conn.sendall(outbound.seal(self._pasteboard(message[4:8])))
        except (EOFError, ConnectionError):
            pass

    @staticmethod
    def _hang_up(conn: socket.socket) -> None:
        # The end of the stream goes out after all of it. Closing with the
        # client's bytes unread would reset the connection and drop what had
        # not gone out yet, the end included; a client on macOS then waited
        # in recv until it sent something.
        conn.shutdown(socket.SHUT_WR)
        try:
            while conn.recv(1 << 16):
                pass
        except OSError:
            pass

    def _pasteboard(self, echo: bytes) -> bytes:
        compressor = zlib.compressobj(9)
        packed = compressor.compress(self.pasteboard) + compressor.flush(
            zlib.Z_SYNC_FLUSH
        )
        return (
            struct.pack(">BxBx", 0x1F, 0)
            + echo
            + struct.pack(">II", len(self.pasteboard), len(packed))
            + packed
        )
