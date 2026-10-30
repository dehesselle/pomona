# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

import getpass
from pathlib import Path

import fakemac
import pytest

from pomona import main
from pomona.cli import split_target

FIXTURE = (Path(__file__).parent / "fixtures" / "adaptive.rfb").read_bytes()


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("pomona ")


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("mac.local", ("mac.local", 5900)),
        ("127.0.0.1:5901", ("127.0.0.1", 5901)),
        ("[::1]", ("::1", 5900)),
        ("[::1]:5902", ("::1", 5902)),
    ],
)
def test_split_target(target, expected):
    assert split_target(target) == expected


def test_probe_against_the_fake_mac(monkeypatch, capsys):
    mac = fakemac.FakeMac(FIXTURE)
    monkeypatch.setenv("POMONA_PASSWORD", mac.password)
    with pytest.raises(SystemExit) as exc:
        main([f"127.0.0.1:{mac.port}", "-u", mac.user, "--probe", "2"])
    mac.join()
    out = capsys.readouterr().out
    assert exc.value.code == 0, out
    assert "signed in: 1440x900 'Fake Mac'" in out
    assert "encoding  1011:" in out


@pytest.mark.parametrize(
    ("raised", "code", "err"),
    [(KeyboardInterrupt, 130, ""), (EOFError, 1, "pomona: no password given\n")],
)
def test_leaving_the_prompt_shows_no_traceback(monkeypatch, capsys, raised, code, err):
    def ask(prompt):
        raise raised

    monkeypatch.delenv("POMONA_PASSWORD", raising=False)
    monkeypatch.setattr(getpass, "getpass", ask)
    with pytest.raises(SystemExit) as exc:
        main(["127.0.0.1:1", "-u", "me"])
    assert exc.value.code == code
    assert capsys.readouterr().err == err


def test_a_refused_sign_in_exits_1(monkeypatch, capsys):
    mac = fakemac.FakeMac()
    monkeypatch.setenv("POMONA_PASSWORD", "wrong")
    with pytest.raises(SystemExit) as exc:
        main([f"127.0.0.1:{mac.port}", "-u", mac.user, "--probe", "1"])
    mac.join()
    assert exc.value.code == 1
    assert "refused the sign-in" in capsys.readouterr().err
