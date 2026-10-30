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
    host, port = split_target(args.target)
    # From the environment or a prompt; never from a pipe on stdin, which made
    # every sign-in fail when PowerShell fed it.
    password = os.environ.get("POMONA_PASSWORD") or getpass.getpass(
        f"Password for {args.user} at {host}: "
    )
    try:
        if args.probe is not None:
            return _probe(host, port, password, args)
        return _window(host, port, password, args)
    except (PomonaError, OSError, RuntimeError) as error:
        print(f"pomona: {error}", file=sys.stderr)
        return 1


def _window(host: str, port: int, password: str, args: argparse.Namespace) -> int:
    from pomona.window import run_window

    updates = 0

    def count(_update: stream.FramebufferUpdate) -> None:
        nonlocal updates
        updates += 1

    session = connect(
        host,
        port,
        user=args.user,
        password=password,
        quality=args.quality,
        push_interval_us=args.push_interval,
        on_update=count,
    )
    start = time.monotonic()
    run_window(
        session, title=f"{args.user} at {host}", ctrl_as_command=args.ctrl_as_command
    )
    elapsed = time.monotonic() - start
    logging.getLogger(__name__).info(
        "%.1f s: %d updates received, %d frames drawn",
        elapsed,
        updates,
        session.picture.frames,
    )
    if session.error is not None:
        print(f"pomona: the session ended: {session.error}", file=sys.stderr)
        return 1
    return 0


def _probe(host: str, port: int, password: str, args: argparse.Namespace) -> int:
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
        user=args.user,
        password=password,
        quality=args.quality,
        push_interval_us=args.push_interval,
        on_update=count,
    )
    with session:
        print(
            f"signed in: {session.server.width}x{session.server.height} {session.server.name!r}"
        )
        session.wait(args.probe)
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
