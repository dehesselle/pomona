# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""The Mac's pasteboard archive, as far as text goes.

The Mac sends every flavor of every item on its pasteboard, so a short text
selection can come with megabytes of images. The archive is a run of items,
each a `u32` flavor count and that many flavors; a flavor is a counted name, a
reserved `u32`, a `u32` count of counted key/value tags, and counted data, every
count a big-endian `u32`. `text` inflates only as far as the text flavor.
"""

import struct
import zlib

from pomona.errors import ProtocolError

TEXT_FLAVOR = b"public.utf8-plain-text"
_U32 = struct.Struct(">I")
_CHUNK = 1 << 16


def archive(text: str) -> bytes:
    """An archive holding `text`, uncompressed. Empty text is an item with no
    flavors, which clears the Mac's pasteboard: a text flavor with no data would
    be a promise, and a paste on the Mac would wait for it."""
    if not text:
        return _U32.pack(0)
    data = text.encode()
    return b"".join(
        (
            _U32.pack(1),
            _U32.pack(len(TEXT_FLAVOR)),
            TEXT_FLAVOR,
            _U32.pack(0),  # reserved
            _U32.pack(0),  # tags
            _U32.pack(len(data)),
            data,
        )
    )


def deflate(data: bytes) -> bytes:
    """One zlib stream, sync-flushed and left open, as the Mac writes its own."""
    compressor = zlib.compressobj(9)
    return compressor.compress(data) + compressor.flush(zlib.Z_SYNC_FLUSH)


def text(compressed: bytes, uncompressed: int) -> str | None:
    """The text on the Mac's pasteboard: "" for an empty one, None for one that
    holds no text. Raises ProtocolError for an archive that does not read."""
    archive = _Inflated(compressed, uncompressed)
    while not archive.done:
        for _ in range(archive.number()):
            name = archive.take(archive.number())
            archive.number()  # reserved
            for _ in range(archive.number()):
                archive.skip(archive.number())  # key
                archive.skip(archive.number())  # value
            size = archive.number()
            if name == TEXT_FLAVOR and size:
                data = archive.take(size)
                try:
                    return data.decode()
                except UnicodeDecodeError as error:
                    raise ProtocolError(f"the pasteboard's text: {error}") from None
            archive.skip(size)
    return "" if uncompressed <= _U32.size else None


class _Inflated:
    """The archive's bytes, inflated as they are read."""

    def __init__(self, compressed: bytes, uncompressed: int) -> None:
        self._inflater = zlib.decompressobj()
        self._input = compressed
        self._buffer = b""
        self._left = uncompressed

    @property
    def done(self) -> bool:
        return self._left <= 0

    def number(self) -> int:
        return _U32.unpack(self.take(_U32.size))[0]

    def take(self, count: int) -> bytes:
        if count > self._left:
            raise ProtocolError(
                f"the pasteboard archive asks for {count} bytes, {self._left} are left"
            )
        while len(self._buffer) < count:
            self._more(count - len(self._buffer))
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        self._left -= count
        return data

    def skip(self, count: int) -> None:
        while count > 0:
            step = min(count, _CHUNK)
            self.take(step)
            count -= step

    def _more(self, wanted: int) -> None:
        source = self._inflater.unconsumed_tail or self._input
        self._input = b""
        if not source:
            raise ProtocolError("the pasteboard archive is cut short")
        try:
            self._buffer += self._inflater.decompress(source, max(wanted, _CHUNK))
        except zlib.error as error:
            raise ProtocolError(f"the pasteboard archive: {error}") from None
