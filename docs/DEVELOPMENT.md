# Development

How to build, test and lint Pomona, and how code from other projects is kept in it.

## Layout

- `src/pomona/`: the Python package: protocol, session, CLI.
- `crates/pomona-native/`: the Rust extension, imported as `pomona._native`: decoding
  and the window. maturin builds it from `pyproject.toml`.
- `crates/apple-adaptive/`: zDesk's Adaptive decoder, taken over unchanged (see
  [Code from other projects](#code-from-other-projects)).
- `tests/`: pytest tests and their recorded fixtures.
- `tools/`: scripts for trying things on a devbox (`window_check.py`, `input_check.py`).

## Where to build and test

All building and testing happens on devboxes, not on the development PC: Linux at
Hetzner for builds and headless tests, Windows at Clouding for the window. A Linux box
needs:

```sh
apt-get install -y build-essential pkg-config python3-dev
curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal -c clippy,rustfmt
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install reuse
uv tool install "ruff~=0.16.0"
```

`python3-dev` is for `cargo test`: without maturin, PyO3 links the test binaries against
libpython.

Copy the working tree over with `tar` or `rsync`, leaving out `target/`, `.venv/`,
`.devbox/` and `.git/`.

## Build

```sh
uv sync
```

This builds the extension through maturin and installs everything into `.venv`. When a
Rust file changes, the next `uv sync` or `uv run` rebuilds it (`tool.uv.cache-keys` in
`pyproject.toml`).

Without cargo on the `PATH`, maturin would install a Rust toolchain on its own through
puccinialin: it would silently download more than 2 GiB of data in the background,
without the user's consent. `build-constraint-dependencies` in `pyproject.toml` allows
no version of puccinialin, so a build without Rust fails at once instead, with uv
reporting that puccinialin cannot be resolved. `uv.lock` records the constraint.

The version lives in `Cargo.toml` (`workspace.package.version`) and is bumped by hand.
maturin hands it to the Python package, and `pomona --version` reads it from the
installed metadata.

## Test

```sh
cargo test --workspace
uv run pytest
```

`tests/fixtures/adaptive.rfb` is the start of a decrypted RFB stream recorded from a
NoMac Mac (macOS 26.6, 1440×900) showing a scrolling Terminal: everything after the
record layer came up, cut after the 196th message. `tests/test_adaptive_replay.py`
replays it and checks the framebuffer's hash at three points. Those hashes were checked
once against an independent pure-Python port of the decoder.

`tests/fixtures/zlib.rfb` and `zrle.rfb` are the same for a session listing zlib first
and one listing ZRLE first, each cut after its last whole message before 2.4 MB: the
whole screen, a pointer shape, then the scrolling window. `tests/test_full_replay.py`
checks the native decoders against `tests/reference.py`, plain-Python decoders written
apart from them, after every update.

`tests/fakemac.py` stands in for a Mac. It speaks the server's side of the type-30
sign-in, ServerInit, the prelude and the rekey, then replays a stream through the record
layer in records of uneven sizes and keeps what the client sent. Its record layer is
written apart from `pomona.record`, so a mistake made the same way on both sides still
fails. It answers a pasteboard fetch with an archive the test gives it.
`tests/test_session.py`, `test_pasteboard.py` and `test_window.py` (reconnecting,
without a window) run whole sessions against it.

A live test against a NoMac Mac is the only check of what the fake cannot know. Through
an ssh forward to the Mac's port 5900:

```sh
POMONA_PASSWORD=... uv run pomona 127.0.0.1:PORT -u USER --probe 15
```

## Lint

```sh
ruff check
ruff format --check
cargo fmt -p pomona-native --check
cargo clippy --workspace --all-targets
reuse lint
```

rustfmt runs on our crate only, because `apple-adaptive` keeps upstream's formatting.

## CI

`.github/workflows/build.yml` runs on GitHub for every push and pull request:

- **Lint**, on Linux: everything under [Lint](#lint), with clippy's warnings as errors.
- **Test**, on Windows, macOS and Linux: clippy again, because each system compiles
  only its own `cfg` code, then `cargo test --workspace` and `uv run --locked pytest`.
- **Build**: a wheel for each of Windows x86_64, macOS arm64 and Linux x86_64, and an
  import of it under Python 3.12. The extension is built for abi3, so each wheel serves
  Python 3.12 and later. There is no sdist.
  - macOS: `MACOSX_DEPLOYMENT_TARGET` is 11.0, the oldest macOS the wheel runs on,
    whatever the runner's own.
  - Linux: built in PyPA's `manylinux_2_28_x86_64` image, because PyPI takes no wheel
    tagged plain `linux`, and one built on the runner would need its newer glibc.
    `MATURIN_PEP517_ARGS` asks maturin for `manylinux_2_28`; maturin then refuses a
    wheel that links anything outside that policy. It is set in the workflow only:
    in `pyproject.toml` it would fail `uv sync` on a newer system. The image is pinned
    by digest and bumped by hand.
  - Each uploads its own artifact, `dist-<name>`.

A `v*` tag must name the version in `Cargo.toml`; each build checks it against its
wheel's. A tag then makes a draft GitHub release with the three wheels and publishes
them to PyPI, through a trusted publisher and the `pypi` environment.

The ruff pin appears in three places: `pyproject.toml`, the setup above, and the
workflow. They change together.

## Trying the window

The window is tried on a Windows devbox at Clouding, and on Linux under Xvfb on the
Hetzner box (see [On Linux](#on-linux)). Neither has a GPU; wgpu draws with the system's
software renderer.

**Setting the box up.** Rust's MSVC target needs Microsoft's C++ build tools, and the
image has no package manager, so everything comes from its own installer:

```powershell
Invoke-WebRequest https://aka.ms/vs/17/release/vs_BuildTools.exe -OutFile vs_BuildTools.exe
.\vs_BuildTools.exe --quiet --wait --norestart --nocache `
  --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended
Invoke-WebRequest https://win.rustup.rs/x86_64 -OutFile rustup-init.exe
.\rustup-init.exe -y --profile minimal -c clippy,rustfmt
rustup default stable
irm https://astral.sh/uv/install.ps1 | iex
```

In Windows PowerShell 5.1, a native program's stderr becomes an error record, so a script
with `$ErrorActionPreference = "Stop"` ends at the first warning. Redirect with `*>` to
a file instead.

**Copying the tree.** Pack it into a file and copy that with `scp`: binary piped through
an ssh session into PowerShell 5.1 is not safe. GNU tar reads `C:` in a file name as a
host, so redirect its output (`tar -cf - . > pomona.tar`) rather than naming the file.
The box's own `tar -xf` unpacks it.

**A desktop to draw on.** An ssh session runs in session 0, which has no desktop: the
window fails with "the window cannot be drawn to". Turn on auto-logon and restart, and the
box keeps a console session with a desktop:

```powershell
$k = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
Set-ItemProperty $k AutoAdminLogon '1'
Set-ItemProperty $k DefaultUserName 'Administrator'
Set-ItemProperty $k DefaultDomainName '.'
Set-ItemProperty $k DefaultPassword '<the box password>'
shutdown /r /t 2
```

This keeps the box's password in its registry, which is fine for a throwaway box only.
`query user` then shows `administrator  console  Active`. Run things in that session with
a scheduled task, `/it` for interactive, and wait for a file the task writes at the end:

```powershell
schtasks /create /tn window-check /tr "C:\setup\check.cmd" /sc once /st 23:59 /ru Administrator /it /f
schtasks /run /tn window-check
```

`tools/window_check.py` opens the window against the fake Mac replaying a fixture, or a
real Mac's port, and prints the updates received and the frames drawn. With `--shot` it
saves a screenshot to look at:

```sh
uv run python tools/window_check.py --fixture zlib.rfb --quality full --seconds 6 --shot C:\setup\shot.png
uv run python tools/window_check.py --port 15900 --seconds 20 --shot C:\setup\shot.png
```

The fake Mac ends the session after 5 s without traffic (its `idle`). Over RDP, the same
window can be used by hand.

`tools/input_check.py` tries input: it opens the window on the fake Mac, types and
clicks into it, and compares what the fake Mac received with what it should have,
printing PASS or FAIL:

```sh
uv run python tools/input_check.py
```

- It sends input through `SendInput`, from a process of its own, as a keyboard and
  mouse would. It moves the pointer with `SendInput` too: `SetCursorPos`'s move can
  arrive after the clicks that follow it.
- It brings the window forward by clicking its title bar, as a user would. Windows let
  neither the window's own process nor another bring it forward with
  `SetForegroundWindow` every time.
- It waits for the first frame before typing: the window exists before it can draw,
  and its grab is set up along with the drawing.
- A disconnected RDP session has no desktop either. `tscon 1 /dest:console` hands it
  back to the console.

**A live Mac on the box.** qem runs on the PC, so the box reaches the Mac through it:
`qem ssh --forward 15900:5900` on the PC, then
`ssh -F .devbox/ssh_config -N -R 127.0.0.1:15900:127.0.0.1:15900 clouding-windows`. A
scheduled task does not inherit the ssh session's environment, so hand it the password in
a file it reads once and deletes.

### On Linux

The Linux box needs no desktop: Xvfb is an X server without a screen, and Mesa's
software drivers draw on it. On top of the setup under
[Where to build and test](#where-to-build-and-test):

```sh
apt-get install -y xvfb xauth libxkbcommon-x11-0 libvulkan1 mesa-vulkan-drivers \
  libegl1 libgl1-mesa-dri imagemagick
xvfb-run -a -s "-screen 0 1440x900x24" \
  uv run python tools/window_check.py --fixture zlib.rfb --quality full --seconds 6 --shot shot.png
```

The log names the adapter: `llvmpipe (...) (Vulkan, Cpu)` is lavapipe. With
`VK_ICD_FILENAMES=/nonexistent`, Vulkan finds no driver and OpenGL draws instead,
`(Gl, Cpu)`. The screenshot comes from ImageMagick's `import`. `xvfb-run` starts no
window manager, so the window has no frame.

**Input on X11** needs a window manager, for the focus and the title bar.
`input_check.py` drives the window with xdotool on an Xvfb of its own, with openbox:

```sh
apt-get install -y openbox xdotool x11-utils
Xvfb :5 -screen 0 1920x1200x24 &
DISPLAY=:5 openbox &
DISPLAY=:5 uv run python tools/input_check.py
```

- Alt-Tab is left out: on Linux the desktop keeps it.
- X is held for a second and the X server repeats it at its own rate, so the check
  keeps the first three presses.
- One notch from xdotool reaches the Mac twice: xdotool presses button 5, the X server
  adds an emulated scroll motion, and winit 0.30 counts both. A mouse's notch comes as
  the motion, and winit skips its emulated button. The check expects the two.

**Wayland** runs headless in sway; grim takes the screenshot:

```sh
apt-get install -y sway grim
export XDG_RUNTIME_DIR=/tmp/xdg; mkdir -m 700 $XDG_RUNTIME_DIR
printf 'output HEADLESS-1 resolution 1440x900\nxwayland disable\n' > sway.conf
WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 WLR_RENDERER=pixman sway -c sway.conf &
WAYLAND_DISPLAY=wayland-1 uv run python tools/window_check.py --fixture zlib.rfb --seconds 6 --shot shot.png
```

sway tiles, so the window fills the output. Keys can be sent with `wtype` while
another `wtype -s 20000` holds a virtual keyboard: each call makes a keyboard of its
own, and without one held, its keys come before winit has bound it. wtype's keymap
has one level per key, so Shift does not change a letter. `wlrctl` moves the pointer,
but its clicks come before winit has bound the pointer it makes for them, and nothing
holds one. Clicks and the wheel on Wayland are therefore untried.

## Code from other projects

What is taken from where, and why it is kept apart, is in
[DESIGN.md](DESIGN.md#code-from-other-projects). The mechanics:

- `crates/apple-adaptive/src/decoder.rs` (MIT, © 2026 Andrew Chen) carries no header, so
  `REUSE.toml` annotates it, and the crate keeps zDesk's `LICENSE`, which the wheel
  ships. To update it, copy the new file over unchanged and record the zDesk commit in
  the crate's `Cargo.toml`.
- New code from another project gets its own crate or subpackage, its license text in
  `LICENSES/`, and the license added to `license` in `pyproject.toml`.

Code we write ourselves is MIT too. Every file carries the SPDX header `reuse annotate`
writes; files that cannot are covered in `REUSE.toml`.
