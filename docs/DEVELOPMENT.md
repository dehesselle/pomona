# Development

How to build, test and lint Pomona, and how code from other projects is kept in it.

## Layout

- `src/pomona/`: the Python package: protocol, session, CLI.
- `crates/pomona-native/`: the Rust extension, imported as `pomona._native`. maturin
  builds it from `pyproject.toml`.
- `crates/apple-adaptive/`: zDesk's Adaptive decoder, taken over unchanged (see
  [Code from other projects](#code-from-other-projects)).
- `tests/`: pytest tests and their recorded fixtures.

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

Copy the working tree over with `tar` or `rsync`, leaving out `research/`, `POMONA.md`,
`target/`, `.venv/`, `.devbox/` and `.git/`. Ruff would otherwise lint and format the
research files too.

## Build

```sh
uv sync
```

This builds the extension through maturin and installs everything into `.venv`. When a
Rust file changes, the next `uv sync` or `uv run` rebuilds it (`tool.uv.cache-keys` in
`pyproject.toml`).

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

`tests/fakemac.py` stands in for a Mac. It speaks the server's side of the type-30
sign-in, ServerInit, the prelude and the rekey, then replays a stream through the record
layer in records of uneven sizes and keeps what the client sent. Its record layer is
written apart from `pomona.record`, so a mistake made the same way on both sides still
fails. `tests/test_session.py` runs whole sessions against it.

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
