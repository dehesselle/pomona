# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""The pasteboard archive, and the pasteboard both ways through a session."""

import struct
import threading
import zlib

import fakemac
import pytest
from test_session import connect_to

from pomona import messages, pasteboard
from pomona.errors import ProtocolError

PASTEBOARD_CHANGED = bytes.fromhex("1400000400010002")


def counted(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def flavor(
    name: bytes, data: bytes, tags: tuple[tuple[bytes, bytes], ...] = ()
) -> bytes:
    """A flavor as the Mac packs it: name, reserved word, tags, data."""
    packed = counted(name) + struct.pack(">II", 0, len(tags))
    for key, value in tags:
        packed += counted(key) + counted(value)
    return packed + counted(data)


def item(*flavors: bytes) -> bytes:
    return struct.pack(">I", len(flavors)) + b"".join(flavors)


def read(archive: bytes) -> str | None:
    compressor = zlib.compressobj(9)
    packed = compressor.compress(archive) + compressor.flush(zlib.Z_SYNC_FLUSH)
    return pasteboard.text(packed, len(archive))


def test_text_is_found_among_other_flavors_and_items():
    image = bytes(range(256)) * 4000
    archive = item(
        flavor(b"public.tiff", image, ((b"NSPboardType", b"NSTIFFPboardType"),)),
        flavor(b"public.rtf", b"{\\rtf1 ...}"),
    ) + item(
        flavor(b"public.file-url", b"file:///tmp/a"),
        flavor(pasteboard.TEXT_FLAVOR, "Grüße €".encode()),
    )
    assert read(archive) == "Grüße €"


def test_an_empty_pasteboard_is_empty_text_and_one_without_text_none():
    assert read(item()) == ""
    assert read(item(flavor(b"public.png", b"\x89PNG"))) is None


def test_an_archive_cut_short_does_not_read():
    archive = item(flavor(pasteboard.TEXT_FLAVOR, b"hello"))
    compressor = zlib.compressobj(9)
    packed = compressor.compress(archive) + compressor.flush(zlib.Z_SYNC_FLUSH)
    with pytest.raises(ProtocolError):
        pasteboard.text(packed[:8], len(archive))


def test_text_goes_out_as_one_item_with_one_flavor():
    assert pasteboard.archive("Grüße") == item(
        flavor(pasteboard.TEXT_FLAVOR, "Grüße".encode())
    )
    # Empty text is an item with no flavors, which clears the Mac's pasteboard.
    assert pasteboard.archive("") == item()


def test_the_set_message_carries_both_sizes_and_the_stream():
    message = messages.set_pasteboard("hello")
    kind, promises, echo, size, packed = struct.unpack_from(">BxBxIII", message)
    assert (kind, promises, echo) == (0x1F, 0, 0)
    assert len(message) == 16 + packed
    archive = zlib.decompressobj().decompress(message[16:])
    assert len(archive) == size
    assert archive == item(flavor(pasteboard.TEXT_FLAVOR, b"hello"))


def test_a_change_at_the_mac_is_fetched_and_handed_on():
    archive = item(
        flavor(b"public.html", b"<b>hi</b>"),
        flavor(pasteboard.TEXT_FLAVOR, b"hi there"),
    )
    mac = fakemac.FakeMac(PASTEBOARD_CHANGED, pasteboard=archive)
    got = []
    arrived = threading.Event()

    def on_pasteboard(text):
        got.append(text)
        arrived.set()

    with connect_to(mac, on_pasteboard=on_pasteboard) as session:
        assert arrived.wait(10)
        assert session.pasteboard == "hi there"
    mac.join()
    assert got == ["hi there"]
    assert messages.fetch_pasteboard(1) in mac.received


def test_text_set_locally_reaches_the_mac():
    mac = fakemac.FakeMac()
    with connect_to(mac) as session:
        session.set_pasteboard("from the PC")
    mac.join()
    sent = [m for m in mac.received if m[0] == 0x1F]
    assert len(sent) == 1
    archive = zlib.decompressobj().decompress(sent[0][16:])
    assert archive == item(flavor(pasteboard.TEXT_FLAVOR, b"from the PC"))
