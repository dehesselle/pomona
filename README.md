# Pomona

> _Pomona (Latin: [poːˈmoːna]) was a goddess of fruitful abundance and plenty in ancient Roman religion and myth. Her name comes from the Latin word pomum, "fruit", specifically orchard fruit._
>
>_source: [Wikipedia](https://en.wikipedia.org/wiki/Pomona_(mythology))_

Pomona is an Apple Screen Sharing client (RFB 003.889), both as a Python library and a
command-line tool. It speaks Standard mode with Adaptive quality, which runs over a
single TCP connection. That connection can be forwarded over ssh, which High Performance
mode cannot. A Rust extension does the decoding and draws the window.

Pomona builds on [zDesk](https://github.com/andrewtheguy/zdesk) by Andrew Chen: its
notes on the protocol were invaluable and its Adaptive decoder is included verbatim.

**Status:** early development. It shows the Mac's screen in a window, passes the
keyboard, mouse and pasteboard text to it, and reconnects when the connection drops;
tried on Windows.

## Install and use

Releases have wheels for Windows x86_64, macOS on Apple silicon and Linux x86_64, for
Python 3.12 and later. From a checkout, with [uv](https://docs.astral.sh/uv/) and a
Rust toolchain:

```sh
uv sync
uv run pomona mac.example.com -u me
```

Pomona asks for the account's password. To reach the Mac through ssh, see
[the manual](docs/MANUAL.md#reaching-the-mac).

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
- [docs/roadmap.md](docs/roadmap.md): what is still to be built, in order

## AI disclosure

Substantial parts of this code were generated with Opus 5.x. Generated code was
tested, but not every line was reviewed in detail.

## License

[MIT](LICENSE). `crates/apple-adaptive` is zDesk's Adaptive decoder by Andrew Chen,
under [its own copyright notice](crates/apple-adaptive/LICENSE).
