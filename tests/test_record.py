# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

import secrets

import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fakemac import _Records

from pomona import record
from pomona.errors import ProtocolError

KEY, IV = bytes(range(16)), bytes(range(16, 32))


class _Wire:
    """Enough of a socket for `_Records.open`: hands out bytes it was given."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    def recv(self, count: int) -> bytes:
        chunk, self.data = self.data[:count], self.data[count:]
        return chunk


def test_records_round_trip_in_any_cut():
    writer, reader = record.RecordWriter(KEY, IV), record.RecordReader(KEY, IV)
    messages = [b"", b"x", bytes(15), secrets.token_bytes(200_000), b"last"]
    wire = b"".join(writer.frame(m) for m in messages)
    received = b""
    for start in range(0, len(wire), 777):
        received += reader.feed(wire[start : start + 777])
    assert received == b"".join(messages)


def test_a_long_message_spans_full_records():
    framed = record.RecordWriter(KEY, IV).frame(bytes(record.MAX_BODY + 1))
    first = int.from_bytes(framed[:2], "big")
    assert first == record.MAX_CIPHERTEXT
    # One full record, then one for the last byte.
    assert len(framed) == 2 + first + 2 + 32


def test_records_match_the_fake_macs_framing():
    """Both directions against an implementation written apart from ours."""
    message = secrets.token_bytes(1000)
    ours = record.RecordWriter(KEY, IV).frame(message)
    assert _Records(KEY, IV, encrypt=False).open(_Wire(ours)) == message
    theirs = _Records(KEY, IV, encrypt=True).seal(message)
    assert record.RecordReader(KEY, IV).feed(theirs) == message


def test_the_chain_and_counter_run_across_records():
    writer, reader = record.RecordWriter(KEY, IV), record.RecordReader(KEY, IV)
    first, second = writer.frame(b"one"), writer.frame(b"two")
    assert reader.feed(first) == b"one"
    assert reader.feed(second) == b"two"
    # A fresh reader starts at sequence 0 with the original IV: the second
    # record does not open on its own.
    with pytest.raises(ProtocolError, match="integrity"):
        record.RecordReader(KEY, IV).feed(second)


def test_a_damaged_record_is_refused():
    framed = bytearray(record.RecordWriter(KEY, IV).frame(b"hello"))
    framed[-1] ^= 1
    with pytest.raises(ProtocolError, match="record 0 failed its integrity check"):
        record.RecordReader(KEY, IV).feed(bytes(framed))


def test_a_misframed_length_is_refused():
    with pytest.raises(ProtocolError, match="not a multiple of 16"):
        record.RecordReader(KEY, IV).feed(b"\x00\x21" + bytes(33))


def test_unwrap_rekey():
    wrap = secrets.token_bytes(16)
    key, iv = secrets.token_bytes(16), secrets.token_bytes(16)
    wrapped = Cipher(algorithms.AES(wrap), modes.ECB()).encryptor().update(key + iv)
    assert record.unwrap_rekey(wrap, b"\0\0\0\7" + wrapped) == (7, key, iv)
