# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Open Pomona's window for a while and report what it drew.

Run on a Windows devbox inside a desktop session, or on a Linux devbox under
Xvfb (see docs/DEVELOPMENT.md, "Trying the window"), against the fake Mac
replaying a fixture, or against a real Mac's forwarded port with
POMONA_PASSWORD set:

    python tools/window_check.py --fixture zlib.rfb --seconds 10 --shot shot.png
    python tools/window_check.py --port 15900 --user agent --seconds 20 --shot shot.png

It prints the updates received, the frames drawn and the bandwidth. With
--shot, a screenshot of the whole screen is saved half way through.
"""

import argparse
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCREENSHOT = (
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
    "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
    "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
    "$g=[System.Drawing.Graphics]::FromImage($i);"
    "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
    "$i.Save('{path}')"
)


def screenshot(path: str) -> None:
    if sys.platform == "win32":
        command = ["powershell", "-NoProfile", "-Command", SCREENSHOT.format(path=path)]
    elif os.environ.get("WAYLAND_DISPLAY"):
        # winit takes Wayland where there is one; grim needs wlroots' screencopy.
        command = ["grim", path]
    else:
        # ImageMagick, from the X server in $DISPLAY.
        command = ["import", "-window", "root", path]
    subprocess.run(command, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--fixture", help="replay tests/fixtures/FIXTURE through the fake Mac"
    )
    source.add_argument(
        "--port", type=int, help="a Mac's Screen Sharing on 127.0.0.1:PORT"
    )
    parser.add_argument("--user", default="agent")
    parser.add_argument("--quality", choices=("adaptive", "full"), default="adaptive")
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--shot", help="save a screenshot here half way through")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    from pomona import connect, run_window

    if args.fixture:
        sys.path.insert(0, str(ROOT / "tests"))
        import fakemac

        mac = fakemac.FakeMac((ROOT / "tests" / "fixtures" / args.fixture).read_bytes())
        port, user, password = mac.port, mac.user, mac.password
    else:
        port, user, password = args.port, args.user, os.environ["POMONA_PASSWORD"]

    updates = 0

    def count(_update: object) -> None:
        nonlocal updates
        updates += 1

    session = connect(
        "127.0.0.1",
        port,
        user=user,
        password=password,
        quality=args.quality,
        on_update=count,
    )
    start = time.monotonic()
    if args.shot:
        threading.Timer(args.seconds / 2, screenshot, (args.shot,)).start()
    threading.Timer(args.seconds, session.close).start()
    run_window(session, title="Pomona window check")
    elapsed = time.monotonic() - start
    frames = session.picture.frames
    print(
        f"{elapsed:.1f} s: {updates} updates ({updates / elapsed:.1f}/s),"
        f" {frames} frames ({frames / elapsed:.1f}/s),"
        f" {session.bytes_received * 8 / elapsed / 1e6:.2f} Mbit/s,"
        f" error: {session.error}"
    )
    return 0 if session.error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
