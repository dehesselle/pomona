# Pomona

A Python library and CLI, with a Rust extension, that shows and controls a Mac's screen
over Apple Screen Sharing's Standard mode (RFB 003.889, TCP only).

Until the research has been reworked into `docs/`, the plan and the protocol findings
are in `POMONA.md` and `research/` (both local, not in git). Read them before protocol
work.

@docs/DEVELOPMENT.md

## Rules

- Build and test only on the devboxes (see docs/DEVELOPMENT.md), never on this PC.
- Commit following the git-commit-message skill. Never push.
- Code from other projects goes into a package of its own, with its license kept
  (docs/DEVELOPMENT.md, "Code from other projects"). `reuse lint` must pass.
- The library never prints, prompts or exits; only the CLI does.
- Live tests may use `qem` (the user's own tool, installed for this user) to start a
  NoMac Mac: always with a cap (`qem start --max-duration 30m --max-cost 1`), and
  `qem stop` as soon as the test is done. If qem lacks something, tell the user
  rather than patching it.
- Keep the README short. A change that affects design, usage or behavior updates the
  matching document in `docs/` in the same change.
