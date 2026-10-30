# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Pomona: a viewer for Apple Screen Sharing, in Standard mode over plain TCP."""

import argparse
from importlib.metadata import version


def main(argv: list[str] | None = None) -> None:
    # prog is fixed because argparse would otherwise name the program after
    # sys.argv[0], which is "__main__.py" under `python -m pomona`.
    parser = argparse.ArgumentParser(
        prog="pomona",
        description="Show and control a Mac's screen over Apple Screen Sharing.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('pomona')}"
    )
    parser.parse_args(argv)
