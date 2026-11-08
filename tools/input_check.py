# SPDX-FileCopyrightText: 2026 René de Hesselle <dehesselle@web.de>
#
# SPDX-License-Identifier: MIT

"""Type and click into Pomona's window, and check what reached the Mac.

Run on a Windows devbox inside a desktop session, or on a Linux devbox on an
X server with a window manager (see docs/DEVELOPMENT.md, "Trying the
window"). It opens the window on the fake Mac, brings it to the front, and
sends keys and mouse events as a keyboard and mouse would, through SendInput
on Windows and xdotool on X11: Shift-H, i, Shift-1, the Windows key, Alt-Tab
(Windows only: on Linux the desktop keeps it), an ä, X held through two
repeats, a left and a right click in the middle of the picture and one notch
of the wheel down. Then it compares what the fake Mac received with what it
should have, and checks that the system kept none of the keys: the window
must still be in front.

    python tools/input_check.py
"""

import ctypes
import json
import struct
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TITLE = "Pomona input check"

WINDOWS = sys.platform == "win32"
if WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)

VK_SHIFT, VK_MENU, VK_TAB, VK_LWIN = 0x10, 0x12, 0x09, 0x5B
EXTENDED = {VK_LWIN}
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSE_LEFT = (0x2, 0x4)
MOUSE_RIGHT = (0x8, 0x10)
MOUSEEVENTF_MOVE, MOUSEEVENTF_WHEEL, MOUSEEVENTF_ABSOLUTE = 0x1, 0x800, 0x8000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class _EVENT(ctypes.Union):
    _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32))


class INPUT(ctypes.Structure):
    _fields_ = (("type", wintypes.DWORD), ("event", _EVENT))


def send(*events: INPUT) -> None:
    array = (INPUT * len(events))(*events)
    if user32.SendInput(len(events), array, ctypes.sizeof(INPUT)) != len(events):
        raise OSError(ctypes.get_last_error(), "SendInput failed")
    time.sleep(0.05)


def key(vk: int, down: bool) -> INPUT:
    # With its scan code, as a keyboard sends it: winit tells keys apart by it.
    scan = user32.MapVirtualKeyW(vk, 0)
    flags = (0 if down else KEYEVENTF_KEYUP) | (
        KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0
    )
    return INPUT(1, _EVENT(ki=KEYBDINPUT(vk, scan, flags, 0, 0)))


def type_unicode(ch: str) -> None:
    """A character as an input method sends it, with no key behind it."""
    for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
        send(INPUT(1, _EVENT(ki=KEYBDINPUT(0, ord(ch), flags, 0, 0))))


def tap(vk: int, *held: int) -> None:
    send(*(key(h, True) for h in held))
    send(key(vk, True))
    send(key(vk, False))
    send(*(key(h, False) for h in reversed(held)))


def mouse(flags: int, data: int = 0) -> INPUT:
    return INPUT(0, _EVENT(mi=MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, 0)))


def move_to(x: int, y: int) -> None:
    """A move as a mouse makes it, in line with the clicks; SetCursorPos's
    move can arrive after them."""
    width, height = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    flags = MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE
    at = ((x * 65536 + width // 2) // width, (y * 65536 + height // 2) // height)
    send(INPUT(0, _EVENT(mi=MOUSEINPUT(*at, 0, flags, 0, 0))))


def click(button: tuple[int, int]) -> None:
    send(mouse(button[0]))
    send(mouse(button[1]))


def xdotool(*args: str) -> str:
    done = subprocess.run(
        ["xdotool", *args], capture_output=True, text=True, check=False
    )
    return done.stdout.strip() if done.returncode == 0 else ""


def find_window() -> int:
    """The window's handle, or 0 while there is none."""
    if WINDOWS:
        return user32.FindWindowW(None, TITLE)
    found = xdotool("search", "--name", TITLE).split()
    return int(found[0]) if found else 0


def start(session, report: dict) -> None:
    """Wait for the window, then drive it from a process of its own, as input
    comes from outside the window."""
    # The window is there before it can draw; it takes the keys once it has
    # drawn its first frame.
    deadline = time.monotonic() + 15
    while not (hwnd := find_window()) or not session.picture.frames:
        if time.monotonic() > deadline:
            report["error"] = "the window never opened"
            session.close()
            return
        time.sleep(0.1)
    driver = subprocess.run(
        [sys.executable, __file__, "--drive", str(hwnd)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report.update(json.loads(driver.stdout))
    except json.JSONDecodeError:
        report["error"] = driver.stdout + driver.stderr
    session.close()


def drive(hwnd: int) -> dict:
    """Bring the window forward as a user would, by clicking its title bar,
    and type and click into it."""
    window = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(window))
    move_to((window.left + window.right) // 2, window.top + 12)
    click(MOUSE_LEFT)
    time.sleep(0.5)
    report = {"in front before": user32.GetForegroundWindow() == hwnd}

    tap(ord("H"), VK_SHIFT)
    tap(ord("I"))
    tap(ord("1"), VK_SHIFT)
    tap(VK_LWIN)
    tap(VK_TAB, VK_MENU)
    type_unicode("ä")
    # Held, as a keyboard repeats: three presses, one release.
    for _ in range(3):
        send(key(ord("X"), True))
    send(key(ord("X"), False))

    client = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    middle = wintypes.POINT(client.right // 2, client.bottom // 2)
    user32.ClientToScreen(hwnd, ctypes.byref(middle))
    move_to(middle.x, middle.y)
    click(MOUSE_LEFT)
    click(MOUSE_RIGHT)
    send(mouse(MOUSEEVENTF_WHEEL, -120))
    time.sleep(0.5)
    report["in front after"] = user32.GetForegroundWindow() == hwnd
    return report


def drive_x11(window: int) -> dict:
    """The same through xdotool, which types through the X server's test
    extension as a keyboard would, into whichever window has the focus."""
    # xwininfo, as xdotool's own geometry is off by the window manager's frame.
    info = subprocess.run(
        ["xwininfo", "-id", str(window)], capture_output=True, text=True, check=True
    ).stdout
    field = {
        key.strip(): value.strip()
        for key, _, value in (line.partition(":") for line in info.splitlines())
    }
    x, y = int(field["Absolute upper-left X"]), int(field["Absolute upper-left Y"])
    width, height = int(field["Width"]), int(field["Height"])
    # The title bar, which the window manager draws above the window.
    xdotool("mousemove", str(x + width // 2), str(max(y - 10, 0)), "click", "1")
    time.sleep(0.5)
    report = {"in front before": xdotool("getactivewindow") == str(window)}

    # Key by key: `key shift+h` would let go of Shift before H.
    for step in (
        ("keydown", "shift", "keydown", "h", "keyup", "h", "keyup", "shift"),
        ("key", "i"),
        ("keydown", "shift", "keydown", "1", "keyup", "1", "keyup", "shift"),
        ("key", "Super_L"),
    ):
        xdotool(*step)
        time.sleep(0.05)
    # xdotool lends ä a spare key for the moment, as an input method would.
    xdotool("type", "--delay", "50", "ä")
    # Held past the X server's repeat delay, so it repeats at its rate.
    xdotool("keydown", "x", "sleep", "1", "keyup", "x")

    xdotool("mousemove", str(x + width // 2), str(y + height // 2))
    time.sleep(0.05)
    # Buttons 1 and 3 are left and right; 5 is a notch of the wheel down.
    for button in ("1", "3", "5"):
        xdotool("click", button)
        time.sleep(0.05)
    time.sleep(0.5)
    report["in front after"] = xdotool("getactivewindow") == str(window)
    return report


def decode(received: list[bytes]) -> list[tuple]:
    """The input among what the Mac received."""
    events = []
    for message in received:
        if message[0] == 0x04:
            down, keysym = struct.unpack(">BxxI", message[1:8])
            events.append(("key", hex(keysym), bool(down)))
        elif message[0] == 0x05:
            events.append(("pointer", *struct.unpack(">BHH", message[1:6])))
        elif message[0] == 0x17:
            # The point deltas, which count up and left as positive.
            dx, dy = struct.unpack(">ii", message[26:34])
            x, y = struct.unpack(">HH", message[54:58])
            events.append(("scroll", -dx, -dy, x, y))
    return events


def tidy(events: list[tuple]) -> list[tuple]:
    """Without plain moves, which come as timing has it: the pointer hovers
    as the window opens, and a move may go out alone or with the click after
    it. Where the pointer went shows in the clicks and releases."""
    tidied = []
    for event in events:
        released = tidied and tidied[-1][0] == "pointer" and tidied[-1][1] != 0
        if event[:2] != ("pointer", 0) or released:
            tidied.append(event)
    return tidied


def three_repeats(events: list[tuple]) -> list[tuple]:
    """At most three presses of a held key, as on Windows: on X11 the server
    repeats it at its own rate for as long as it is held."""
    kept: list[tuple] = []
    i = 0
    while i < len(events):
        event = events[i]
        pair = [event, ("key", event[1], False)]
        if event[0] != "key" or not event[2] or events[i : i + 2] != pair:
            kept.append(event)
            i += 1
            continue
        count = 0
        while events[i : i + 2] == pair:
            if count < 3:
                kept.extend(pair)
            count += 1
            i += 2
    return kept


def same(got: tuple, want: tuple) -> bool:
    """Equal, but for two pixels of position: the middle of the window, in
    SendInput's absolute coordinates, lands a pixel or two off."""
    if got[0] != want[0] or got[0] == "key":
        return got == want
    return got[:-2] == want[:-2] and all(
        abs(a - b) <= 2 for a, b in zip(got[-2:], want[-2:], strict=True)
    )


def main() -> int:
    if sys.argv[1:2] == ["--drive"]:
        print(json.dumps((drive if WINDOWS else drive_x11)(int(sys.argv[2]))))
        return 0
    sys.path.insert(0, str(ROOT / "tests"))
    import fakemac

    from pomona import connect, run_window

    mac = fakemac.FakeMac(idle=30)
    session = connect("127.0.0.1", mac.port, user=mac.user, password=mac.password)
    report: dict = {}
    threading.Thread(target=start, args=(session, report), daemon=True).start()
    run_window(session, title=TITLE)
    mac.join(30)

    x, y = 720, 450
    shift, meta = "0xffe1", "0xffe7"
    expected = [
        ("key", shift, True),
        ("key", "0x48", True),
        ("key", "0x48", False),
        ("key", shift, False),
        ("key", "0x69", True),
        ("key", "0x69", False),
        ("key", shift, True),
        ("key", "0x21", True),
        ("key", "0x21", False),
        ("key", shift, False),
        ("key", "0xffeb", True),
        ("key", "0xffeb", False),
        *(
            [
                ("key", meta, True),
                ("key", "0xff09", True),
                ("key", "0xff09", False),
                ("key", meta, False),
            ]
            if WINDOWS
            else []
        ),
        ("key", "0x10000e4", True),
        ("key", "0x10000e4", False),
        # Each repeat goes up and down again.
        ("key", "0x78", True),
        ("key", "0x78", False),
        ("key", "0x78", True),
        ("key", "0x78", False),
        ("key", "0x78", True),
        ("key", "0x78", False),
        ("pointer", 1, x, y),
        ("pointer", 0, x, y),
        # Right is the Mac's button 2.
        ("pointer", 2, x, y),
        ("pointer", 0, x, y),
        ("scroll", 0, 40, x, y),
        # xdotool's notch is a press of button 5, for which the X server adds
        # an emulated scroll motion. winit 0.30 counts both; a mouse's notch
        # comes as the motion, and winit skips the emulated button.
        *([] if WINDOWS else [("scroll", 0, 40, x, y)]),
    ]
    got = tidy(decode(mac.received))
    if not WINDOWS:
        got = three_repeats(got)
    for event in got:
        print(*event)
    print(report)
    matches = len(got) == len(expected) and all(map(same, got, expected))
    ok = (
        matches
        and report.get("in front before")
        and report.get("in front after")
        and "error" not in report
    )
    if not matches:
        print("expected:")
        for event in expected:
            print(*event)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
