# Pomona

A Python library and CLI, with a Rust extension, that shows and controls a Mac's screen
over Apple Screen Sharing's Standard mode (RFB 003.889, TCP only).

- Read docs/DESIGN.md before architectural changes, and respect its core principles.
- Read docs/protocol.md before touching anything that goes over the wire.
- docs/MANUAL.md has the CLI and library API, docs/OPERATIONS.md runtime behavior,
  docs/measurements.md the numbers and the conditions they were measured under.

@docs/DEVELOPMENT.md

## Rules

- Build and test only on the devboxes (see docs/DEVELOPMENT.md), never on this PC.
- Commit following the git-commit-message skill. Never push.
- Code from other projects goes into a package of its own, with its license kept
  (docs/DESIGN.md, "Code from other projects"). `reuse lint` must pass.
- Live-test whenever useful: start and stop NoMac Macs freely with `qem` (the user's
  own tool, installed for this user), with a cap (`qem start --max-duration 30m
  --max-cost 1`) and `qem stop` when done. If qem lacks something, tell the user
  rather than patching it.
- The README stays short. A change that affects design, usage or behavior updates the
  matching document in docs/ in the same change. DESIGN.md is kept current and carries
  no project history.
