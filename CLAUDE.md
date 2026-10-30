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
- Live tests against a NoMac Mac cost credit: ask the user each time.
- Keep the README short. A change that affects design, usage or behavior updates the
  matching document in `docs/` in the same change.
