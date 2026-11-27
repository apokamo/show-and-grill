# Changelog

All notable changes to show-and-grill are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). A version is a git
tag (`vX.Y.Z`), which `install.sh --version` installs.

## [Unreleased]

### Added

- README: screenshots of an example question page (light and dark), a sample of
  the terminal output, "Why show-and-grill", and "How it works".
- `README.ja.md`, a Japanese translation of the README. The English README is the
  source.
- This changelog.

### Changed

- The agent opens each question page in the browser (`python3 -m webbrowser`).
  The link is still printed in the terminal.
- SKILL.md is shorter: repeated rules are merged, and the steps for the server,
  recording, and stopping say less about why.
- Sources and credits moved from SKILL.md to `docs/CREDITS.md`.

### Removed

- The session log (`log.md`).

## [0.1.0] - 2026-11-16

First public release.

### Added

- The `show-and-grill` skill for Claude Code and Codex: one page with figures and
  tables per question, answers in the terminal, and decisions written into the
  issue body with a provenance comment.
- Settings for display (`visual`, `cui`, `artifact`), depth (`summary`,
  `detail`), and language (`en`, `ja`), and words to switch them while answering.
- A local page server: `scripts/serve.py` (Python), and `scripts/serve.ps1` for
  Windows without Python.
- Four ways to install: the Claude Code plugin, the Codex plugin,
  `npx skills add`, and `install.sh`.

### Fixed

- The page server no longer looks up the host name when it starts. The lookup
  took 35 seconds on a macOS CI runner, so the server did not start in time.

[Unreleased]: https://github.com/apokamo/show-and-grill/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/apokamo/show-and-grill/releases/tag/v0.1.0
