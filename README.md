# Pomona

Pomona shows and controls a Mac's screen over Apple Screen Sharing's own protocol
(RFB 003.889), as a Python library and a command-line tool. It speaks Standard mode with
Adaptive quality, which runs over a single TCP connection. That connection can be
forwarded over ssh, which High Performance mode cannot. A Rust extension does the
decoding and is to draw the window.

**Status:** early development. It signs in and receives the Mac's picture, without a
window yet.

## Install and use

From a checkout, with [uv](https://docs.astral.sh/uv/) and a Rust toolchain:

```sh
uv sync
ssh -N -L 127.0.0.1:5901:127.0.0.1:5900 me@mac &
POMONA_PASSWORD=... uv run pomona 127.0.0.1:5901 -u me --probe 10
```

The macOS account needs Remote Management permission; without it, the Mac refuses the
sign-in exactly as it refuses a wrong password.

## Documentation

- [docs/MANUAL.md](docs/MANUAL.md): the `pomona` command and the Python library
- [docs/OPERATIONS.md](docs/OPERATIONS.md): traffic, pacing, how sessions end,
  troubleshooting
- [docs/DESIGN.md](docs/DESIGN.md): architecture, threads, code from other projects,
  core principles
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): building, testing and linting on devboxes,
  test fixtures, the fake Mac
- [docs/protocol.md](docs/protocol.md): Apple's RFB 003.889 as Pomona speaks it
- [docs/measurements.md](docs/measurements.md): rates, bandwidth and decoding speed, as
  measured
- [docs/high-performance.md](docs/high-performance.md): why High Performance mode does
  not work through ssh
