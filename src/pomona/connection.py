# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""From a connected socket to the record layer: version, sign-in, ServerInit,
the cleartext prelude and the rekey.

Everything here reads exactly the bytes it needs, so nothing of the record
layer is read before it is up.
"""

import hashlib
import secrets
import socket
import struct
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from pomona import messages, record, stream
from pomona.errors import AuthenticationError, ProtocolError

VERSION = b"RFB 003.889\n"
# Apple's Diffie-Hellman sign-in ("Apple Remote Desktop").
SECURITY_DH = 30
# ClientInit: share the screen (0x01), and send the extended ServerInit (0x80).
CLIENT_INIT = 0x81

_MIN_KEY_BYTES = 64
_MAX_KEY_BYTES = 1024
_CREDENTIAL_FIELD = 64

# ServerInit flags.
OBSERVE_ONLY = 0x01
MAY_CONTROL = 0x02
NO_SCREEN_CAPTURE = 0x08


@dataclass(frozen=True, slots=True)
class ServerInit:
    width: int
    height: int
    name: str
    flags: int
    # Which client message types the Mac accepts, one bit each.
    commands: bytes

    def accepts(self, message_type: int) -> bool:
        """Whether the Mac lists `message_type`: bit 7 - t % 8 of byte t / 8."""
        byte = message_type >> 3
        return byte < len(self.commands) and bool(
            self.commands[byte] >> (7 - (message_type & 7)) & 1
        )


def read_exact(sock: socket.socket, count: int) -> bytes:
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise ProtocolError("the Mac closed the connection")
        data += chunk
    return bytes(data)


def _read_u8(sock: socket.socket) -> int:
    return read_exact(sock, 1)[0]


def _read_u16(sock: socket.socket) -> int:
    return struct.unpack(">H", read_exact(sock, 2))[0]


def _read_u32(sock: socket.socket) -> int:
    return struct.unpack(">I", read_exact(sock, 4))[0]


def sign_in(sock: socket.socket, user: str, password: str) -> tuple[bytes, ServerInit]:
    """Version, security type 30, ClientInit and ServerInit. Returns the wrap
    key that the rekey comes under, and the ServerInit."""
    version = read_exact(sock, 12)
    if version != VERSION:
        raise ProtocolError(f"not a Mac: the server speaks {version!r}")
    sock.sendall(VERSION)

    count = _read_u8(sock)
    if count == 0:
        reason = read_exact(sock, _read_u32(sock)).decode(errors="replace")
        raise ProtocolError(f"the Mac refused the connection: {reason}")
    offered = read_exact(sock, count)
    if SECURITY_DH not in offered:
        raise ProtocolError(f"the Mac offers no sign-in Pomona speaks: {list(offered)}")
    sock.sendall(bytes((SECURITY_DH,)))

    wrap_key = _diffie_hellman(sock, user, password)
    result = _read_u32(sock)
    if result != 0:
        raise AuthenticationError(
            f"the Mac refused the sign-in (result {result}): a wrong user name or"
            " password, or an account without Remote Management permission"
        )

    sock.sendall(bytes((CLIENT_INIT,)))
    return wrap_key, _server_init(sock)


def _diffie_hellman(sock: socket.socket, user: str, password: str) -> bytes:
    """Type 30: the Mac's group and public key in, our credentials and public
    key out. The credentials go under AES-128-ECB keyed with MD5 of the shared
    secret, and that same key later unwraps the rekey."""
    generator = _read_u16(sock)
    key_bytes = _read_u16(sock)
    if not _MIN_KEY_BYTES <= key_bytes <= _MAX_KEY_BYTES:
        raise ProtocolError(f"a {key_bytes}-byte Diffie-Hellman key")
    prime = int.from_bytes(read_exact(sock, key_bytes))
    theirs = int.from_bytes(read_exact(sock, key_bytes))
    if prime < 3 or not 1 < theirs < prime - 1:
        raise ProtocolError("a degenerate Diffie-Hellman group or key")

    # Twice the key's length, as Apple's viewer draws it.
    private = int.from_bytes(secrets.token_bytes(2 * key_bytes))
    ours = pow(generator, private, prime).to_bytes(key_bytes)
    # Hashed as bytes, so the secret keeps its leading zeros.
    shared = pow(theirs, private, prime).to_bytes(key_bytes)
    key = hashlib.md5(shared).digest()

    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    sock.sendall(encryptor.update(_credentials(user, password)) + ours)
    return key


def _credentials(user: str, password: str) -> bytes:
    """The user name at 0 and the password at 64, each null-terminated, over
    random filler so the same login never encrypts the same twice."""
    blob = bytearray(secrets.token_bytes(2 * _CREDENTIAL_FIELD))
    for offset, field, what in ((0, user, "user name"), (64, password, "password")):
        data = field.encode()
        if len(data) >= _CREDENTIAL_FIELD:
            raise ValueError(f"the {what} is longer than {_CREDENTIAL_FIELD - 1} bytes")
        blob[offset : offset + len(data) + 1] = data + b"\0"
    return bytes(blob)


def _server_init(sock: socket.socket) -> ServerInit:
    width, height = struct.unpack(">HH", read_exact(sock, 4))
    read_exact(sock, 16)  # the Mac's pixel format, replaced by ours
    field = read_exact(sock, _read_u32(sock))
    # The extended ServerInit's "name" is a zero u16, a u32 of flags, the
    # 16-byte command bitmap, and only then the name.
    if len(field) >= 22 and field[:2] == b"\0\0":
        flags = struct.unpack_from(">I", field, 2)[0]
        return ServerInit(
            width, height, field[22:].decode(errors="replace"), flags, field[6:22]
        )
    return ServerInit(width, height, field.decode(errors="replace"), 0, bytes(16))


def start_records(sock: socket.socket, wrap_key: bytes) -> tuple[bytes, bytes]:
    """Send the cleartext prelude and wait for the rekey; return its key and IV.

    The prelude goes back to back, as the Mac switches to records the moment it
    sends the rekey. The pasteboard must be turned on here, in cleartext.
    """
    sock.sendall(
        messages.viewer_info()
        + messages.set_mode_control()
        + messages.auto_pasteboard(True)
        + messages.set_encryption_start()
        + messages.set_encryption_decrypt_inbound()
    )
    while True:
        kind = _read_u8(sock)
        if kind == stream.FRAMEBUFFER_UPDATE:
            _pad, count = struct.unpack(">BH", read_exact(sock, 3))
            if count == 0:
                continue
            *_geometry, encoding = struct.unpack(">HHHHi", read_exact(sock, 12))
            if encoding != stream.REKEY:
                raise ProtocolError(
                    f"encoding {encoding} before the record layer was up"
                )
            if count != 1:
                raise ProtocolError(f"{count - 1} more rectangles after the rekey")
            _generation, key, iv = record.unwrap_rekey(
                wrap_key, read_exact(sock, record.REKEY_LENGTH)
            )
            return key, iv
        if kind == stream.BELL:
            continue
        if kind == stream.MISC_STATUS:
            # A stale pasteboard status can come before the rekey.
            _pad, length = struct.unpack(">BH", read_exact(sock, 3))
            read_exact(sock, length)
            continue
        raise ProtocolError(f"message type 0x{kind:02x} before the record layer was up")
