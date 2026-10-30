# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Pomona: a viewer for Apple Screen Sharing, in Standard mode over plain TCP.

```python
from pomona import connect

with connect("127.0.0.1", 5900, user="me", password=password) as session:
    session.wait(10)
    pixels = session.picture.snapshot()
```
"""

import argparse
from importlib.metadata import version
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pomona.errors import (
        AuthenticationError,
        EndedAtMacError,
        PomonaError,
        ProtocolError,
    )
    from pomona.session import Session, connect
    from pomona.window import run_window

__all__ = [
    "AuthenticationError",
    "EndedAtMacError",
    "PomonaError",
    "ProtocolError",
    "Session",
    "connect",
    "run_window",
]

_LAZY = {
    "connect": "pomona.session",
    "Session": "pomona.session",
    "AuthenticationError": "pomona.errors",
    "EndedAtMacError": "pomona.errors",
    "PomonaError": "pomona.errors",
    "ProtocolError": "pomona.errors",
    "run_window": "pomona.window",
}


def __getattr__(name: str) -> object:
    # Imported on first use, so `pomona --version` does not load the
    # extension and the crypto library.
    if name in _LAZY:
        import importlib

        return getattr(importlib.import_module(_LAZY[name]), name)
    raise AttributeError(f"module 'pomona' has no attribute {name!r}")


def main(argv: list[str] | None = None) -> None:
    # prog is fixed because argparse would otherwise name the program after
    # sys.argv[0], which is "__main__.py" under `python -m pomona`.
    parser = argparse.ArgumentParser(
        prog="pomona",
        description="Show and control a Mac's screen over Apple Screen Sharing.",
        epilog="The password comes from POMONA_PASSWORD, or is asked for.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('pomona')}"
    )
    parser.add_argument(
        "target", metavar="HOST[:PORT]", help="the Mac, port 5900 by default"
    )
    parser.add_argument("-u", "--user", required=True, help="the macOS account")
    parser.add_argument(
        "--probe",
        type=float,
        metavar="SECONDS",
        help="instead of a window: receive for SECONDS and print what arrived",
    )
    parser.add_argument(
        "--quality",
        choices=("adaptive", "full"),
        default="adaptive",
        help="Apple's Adaptive tiles (default), or Full: lossless zlib, ten times the traffic",
    )
    parser.add_argument(
        "--push-interval",
        type=int,
        default=0,
        metavar="US",
        help="pace the Mac's updates, in microseconds (default 0: as fast as it can)",
    )
    parser.add_argument(
        "--ctrl-as-command",
        action="store_true",
        help="send Control as Command and the Windows key as Control",
    )
    parser.add_argument(
        "--no-pasteboard",
        action="store_true",
        help="share no text between the local pasteboard and the Mac's",
    )
    size = parser.add_mutually_exclusive_group()
    size.add_argument(
        "--scale",
        type=float,
        metavar="FACTOR",
        help="open the window at FACTOR times the Mac's screen, at most 1",
    )
    size.add_argument(
        "--size",
        metavar="WxH",
        help="open the window as large as fits in W by H pixels",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    args = parser.parse_args(argv)
    if args.scale is not None and not 0 < args.scale <= 1:
        parser.error("--scale takes a factor above 0 and at most 1")
    if args.size is not None:
        width, _, height = args.size.lower().partition("x")
        if not (width.isdigit() and height.isdigit() and int(width) and int(height)):
            parser.error("--size takes WxH, such as 1920x1080")
        args.size = (int(width), int(height))

    from pomona.cli import run

    raise SystemExit(run(args))
