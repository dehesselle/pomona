# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Builders against the bytes measured on the wire."""

import struct

import pytest

from pomona import messages


def test_set_pixel_format():
    assert messages.set_pixel_format() == bytes.fromhex(
        "00000000 20 18 00 01 00ff 00ff 00ff 10 08 00 000000"
    )


def test_set_encryption():
    assert (
        messages.set_encryption_start().hex(" ", 2) == "1200 0001 0001 0001 0000 0001"
    )
    assert (
        messages.set_encryption_decrypt_inbound().hex(" ", 2) == "1200 0002 0001 0000"
    )


def test_set_encodings_lists_adaptive_first_of_the_codecs():
    msg = messages.set_encodings(messages.ADAPTIVE_ENCODINGS)
    count = struct.unpack_from(">H", msg, 2)[0]
    listed = struct.unpack_from(f">{count}i", msg, 4)
    assert listed == (0, 1100, 1101, 1103, 1104, 1105, 1107, 1109, -223, -224, 1011, 16)
    assert len(msg) == 4 + 4 * count


def test_auto_framebuffer_update():
    assert messages.auto_framebuffer_update(0, 1440, 900) == bytes.fromhex(
        "0900 0001 00000000 0000 0000 05a0 0384"
    )


def test_viewer_info_is_66_bytes_of_numbers():
    msg = messages.viewer_info()
    assert len(msg) == 66
    assert msg[:4] == bytes.fromhex("2100003e")
    assert struct.unpack_from(">HIIII", msg, 4) == (1, 2, 6, 1, 0)
    assert struct.unpack_from(">III", msg, 22) == (26, 6, 2)


def test_scroll_wheel_is_58_bytes_with_the_mac_signs():
    msg = messages.scroll_wheel(40, -25, 100, 200)
    assert len(msg) == 58
    assert msg[:8] == bytes.fromhex("1700 0036 0001 000b")
    assert struct.unpack_from(">3h", msg, 8) == (-4, 2, 0)
    assert struct.unpack_from(">3i", msg, 14) == (-262144, 163840, 0)
    assert struct.unpack_from(">3i", msg, 26) == (-40, 25, 0)
    assert struct.unpack_from(">IIIIHH", msg, 38) == (0, 0, 0, 2, 100, 200)


def test_scroll_wheel_truncates_toward_zero():
    msg = messages.scroll_wheel(-15, 15, 0, 0)
    assert struct.unpack_from(">2h", msg, 8) == (1, -1)


def test_set_server_scaling():
    assert messages.set_server_scaling(0.5) == b"\x08\x00" + struct.pack(">d", 0.5)
    with pytest.raises(ValueError):
        messages.set_server_scaling(1.5)


def test_set_display():
    assert messages.set_display(None) == bytes.fromhex("0d01 0000 00000000")
    assert messages.set_display(4) == bytes.fromhex("0d00 0000 00000004")


def test_input_events():
    assert messages.key_event(0xFF0D, True) == bytes.fromhex("0401 0000 0000ff0d")
    assert messages.pointer_event(0x02, 10, 20) == bytes.fromhex("0502 000a 0014")
