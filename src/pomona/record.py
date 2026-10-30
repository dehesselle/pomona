# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Apple's record layer: everything after the rekey, in both directions.

A record on the wire is::

    u16 ciphertext_len
    AES-128-CBC( u16 body_len || body || filler || SHA1(u32_be(seq) || all before) )

Each direction has one CBC chain and one sequence counter for the whole session,
both started from the key and IV the rekey carried. Records are cut wherever
they are full, so a message can span several of them, and the bodies are put
back together by concatenation.
"""

import hashlib
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from pomona.errors import ProtocolError

BLOCK = 16
TRAILER = 20
_BODY_LENGTH = 2
# The largest record that a u16 can count, rounded down to whole blocks.
MAX_CIPHERTEXT = 0xFFFF // BLOCK * BLOCK
MAX_BODY = MAX_CIPHERTEXT - _BODY_LENGTH - TRAILER
# The rekey rectangle's body: u32 generation, then the wrapped key and IV.
REKEY_LENGTH = 4 + BLOCK + BLOCK

_U16 = struct.Struct(">H")
_U32 = struct.Struct(">I")


def _filler(body_length: int) -> int:
    return -(_BODY_LENGTH + body_length + TRAILER) % BLOCK


_MIN_CIPHERTEXT = _BODY_LENGTH + _filler(0) + TRAILER


def unwrap_rekey(wrap_key: bytes, body: bytes) -> tuple[int, bytes, bytes]:
    """The generation, key and IV out of a rekey body, each block decrypted
    under the wrap key from sign-in."""
    if len(body) != REKEY_LENGTH:
        raise ProtocolError(f"a rekey of {len(body)} bytes, where it is {REKEY_LENGTH}")
    decryptor = Cipher(algorithms.AES(wrap_key), modes.ECB()).decryptor()
    unwrapped = decryptor.update(body[4:]) + decryptor.finalize()
    return _U32.unpack_from(body)[0], unwrapped[:BLOCK], unwrapped[BLOCK:]


def _trailer(sequence: int, covered: bytes | bytearray | memoryview) -> bytes:
    digest = hashlib.sha1(_U32.pack(sequence & 0xFFFFFFFF))
    digest.update(covered)
    return digest.digest()


class RecordWriter:
    """Frames messages into records. Not thread-safe: one writer per session."""

    def __init__(self, key: bytes, iv: bytes) -> None:
        self._cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        self._sequence = 0

    def frame(self, message: bytes) -> bytes:
        """The records that carry `message`: as many full ones as it needs."""
        out = bytearray()
        for start in range(0, max(len(message), 1), MAX_BODY):
            self._record(out, message[start : start + MAX_BODY])
        return bytes(out)

    def _record(self, out: bytearray, body: bytes) -> None:
        plaintext = bytearray(_U16.pack(len(body)))
        plaintext += body
        plaintext += bytes(_filler(len(body)))
        plaintext += _trailer(self._sequence, plaintext)
        self._sequence += 1
        out += _U16.pack(len(plaintext))
        out += self._cipher.update(bytes(plaintext))


class RecordReader:
    """Turns received bytes back into the RFB stream they carry."""

    def __init__(self, key: bytes, iv: bytes) -> None:
        self._cipher = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        self._sequence = 0
        self._buffer = bytearray()

    def feed(self, data: bytes) -> bytes:
        """The bodies of every record completed by `data`, concatenated."""
        self._buffer += data
        out = bytearray()
        position = 0
        buffer = self._buffer
        while position + _BODY_LENGTH <= len(buffer):
            length = _U16.unpack_from(buffer, position)[0]
            if length < _MIN_CIPHERTEXT or length % BLOCK:
                raise ProtocolError(
                    f"a record of {length} ciphertext bytes, not a multiple of"
                    f" {BLOCK} of at least {_MIN_CIPHERTEXT}"
                )
            end = position + _BODY_LENGTH + length
            if end > len(buffer):
                break
            out += self._open(bytes(buffer[position + _BODY_LENGTH : end]))
            position = end
        del buffer[:position]
        return bytes(out)

    def _open(self, ciphertext: bytes) -> bytes:
        plaintext = self._cipher.update(ciphertext)
        covered = len(plaintext) - TRAILER
        if plaintext[covered:] != _trailer(self._sequence, plaintext[:covered]):
            raise ProtocolError(f"record {self._sequence} failed its integrity check")
        self._sequence += 1
        body_length = _U16.unpack_from(plaintext)[0]
        if _BODY_LENGTH + body_length > covered:
            raise ProtocolError(
                f"a record claims a {body_length}-byte body in {len(plaintext)} bytes"
            )
        return plaintext[_BODY_LENGTH : _BODY_LENGTH + body_length]
