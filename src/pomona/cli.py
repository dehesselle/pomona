# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""The `pomona` command, after its arguments are parsed."""

import argparse
import collections
import getpass
import logging
import os
import sys
import time

from pomona import stream
from pomona.errors import PomonaError
from pomona.session import connect

_PICTURE = (stream.ADAPTIVE, stream.ZLIB, stream.ZRLE, stream.RAW)


def split_target(target: str) -> tuple[str, int]:
    """HOST, HOST:PORT, [V6] or [V6]:PORT."""
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") else 5900
    if target.count(":") == 1:
        host, port = target.split(":")
        return host, int(port)
    return target, 5900


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=(logging.WARNING, logging.INFO, logging.DEBUG)[min(args.verbose, 2)],
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if args.probe is None:
        print(
            "pomona: the window is not there yet; use --probe SECONDS", file=sys.stderr
        )
        return 2
    host, port = split_target(args.target)
    # From the environment or a prompt; never from a pipe on stdin, which made
    # every sign-in fail when PowerShell fed it.
    password = os.environ.get("POMONA_PASSWORD") or getpass.getpass(
        f"Password for {args.user} at {host}: "
    )
    try:
        return _probe(host, port, args.user, password, args.probe, args.push_interval)
    except (PomonaError, OSError) as error:
        print(f"pomona: {error}", file=sys.stderr)
        return 1


def _probe(
    host: str, port: int, user: str, password: str, seconds: float, push_interval: int
) -> int:
    rects = collections.Counter()
    sizes = collections.Counter()
    pixels = collections.Counter()
    per_second = collections.Counter()
    first_picture = None
    updates = 0
    start = time.monotonic()

    def count(update: stream.FramebufferUpdate) -> None:
        nonlocal first_picture, updates
        now = time.monotonic() - start
        updates += 1
        per_second[int(now)] += 1
        for rect in update.rects:
            rects[rect.encoding] += 1
            sizes[rect.encoding] += len(rect.body)
            if rect.encoding in _PICTURE:
                pixels[rect.encoding] += rect.w * rect.h
                if first_picture is None:
                    first_picture = now

    session = connect(
        host,
        port,
        user=user,
        password=password,
        push_interval_us=push_interval,
        on_update=count,
    )
    with session:
        print(
            f"signed in: {session.server.width}x{session.server.height} {session.server.name!r}"
        )
        session.wait(seconds)
    elapsed = time.monotonic() - start
    if session.error is not None:
        print(f"session ended: {session.error}")

    screen = (session.picture.width * session.picture.height) or 1
    print(
        f"{elapsed:.1f} s: {session.bytes_received * 8 / elapsed / 1e6:.2f} Mbit/s,"
        f" {updates} updates ({updates / elapsed:.1f}/s)"
    )
    print(
        "first picture after "
        + ("never" if first_picture is None else f"{first_picture:.2f} s")
    )
    for encoding in sorted(rects, key=lambda e: -sizes[e]):
        print(
            f"  encoding {encoding:5d}: {rects[encoding]:5d} rects, {sizes[encoding]:9d} bytes,"
            f" {pixels[encoding] / screen:.1f} screens"
        )
    print("updates per second:", [per_second[s] for s in range(int(elapsed) + 1)])
    return 0 if session.error is None else 1
