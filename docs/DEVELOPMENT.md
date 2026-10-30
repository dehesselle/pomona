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

Code taken from another project lives in a package of its own, never mixed into ours,
so its license and its origin stay clear:

- `crates/apple-adaptive/src/decoder.rs` is zDesk's decoder (MIT, © 2026 Andrew Chen),
  byte for byte at zDesk commit `2072baf3`. It carries no header, so `REUSE.toml`
  annotates it, and the crate keeps zDesk's `LICENSE`. To update it, copy the new file
  over unchanged and record the commit in the crate's `Cargo.toml`.
- Code from iShareScreen (AGPL-3.0-or-later), if any is ever taken, goes into a
  subpackage `src/pomona/iss/` of its own, and the package's `license` gains the term.

Code we write ourselves is MIT too. Every file carries the SPDX header `reuse annotate`
writes; files that cannot are covered in `REUSE.toml`.
