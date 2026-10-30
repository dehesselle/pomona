# Pomona

Pomona shows and controls a Mac's screen over Apple Screen Sharing's own protocol
(RFB 003.889), as a Python library and a command-line tool. It speaks Standard mode with
Adaptive quality, which runs over a single TCP connection. That connection can be
forwarded over ssh, which High Performance mode cannot. A Rust extension does the
decoding and draws the window.

**Status:** early development. It signs in and receives the Mac's picture, without a
window yet: `pomona HOST -u USER --probe 10` reports what arrived.

## Install

From a checkout, with [uv](https://docs.astral.sh/uv/) and a Rust toolchain:

```sh
uv sync
uv run pomona --version
```

## Documentation

- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): building, testing and linting, the devbox
  workflow, and how code from other projects is kept
