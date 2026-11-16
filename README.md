# show-and-grill

A relentless design interview for coding agents (Claude Code and Codex).

Before each question, the agent writes a page with the facts you need to decide: how things work now, how the options differ, and a recommendation, with figures and tables. You open the page from a link in the terminal and answer in the terminal, one question at a time. At the end, the decisions go into the issue body (or your plan file) and a provenance comment.

Use it on purpose, right after an issue or plan is written and before automated work starts. Skip it for small issues.

```text
/show-and-grill 123                 # Claude Code: grill issue 123
/show-and-grill docs/plan.md cui    # a local plan, everything in the terminal
$show-and-grill 123 detail ja       # Codex: longer pages, in Japanese
```

See [skills/show-and-grill/SKILL.md](skills/show-and-grill/SKILL.md) for the full behavior.

## Install

**Pick one way per agent.** If you install the same skill two ways (for example, the plugin and `install.sh`, or in a repository and for the user), the agent can load either copy or list the skill twice.

### 1. Plugin

Claude Code (the skill is then called `/show-and-grill:show-and-grill`):

```bash
claude plugin marketplace add apokamo/show-and-grill
claude plugin install show-and-grill@show-and-grill
```

Codex:

```bash
codex plugin marketplace add apokamo/show-and-grill
```

Then install `show-and-grill` from `/plugins` in Codex.

### 2. `npx skills add`

```bash
npx skills add apokamo/show-and-grill
```

It finds `skills/show-and-grill/` and copies it to the folders your agents read.

### 3. `install.sh`

macOS and Linux (including WSL). It needs `git`.

Install into the current git repository (`.claude/skills/show-and-grill/`, plus a link at `.agents/skills/show-and-grill` for Codex):

```bash
curl -fsSL https://raw.githubusercontent.com/apokamo/show-and-grill/main/install.sh | sh -s -- --version v0.1.0
```

Install for the user, under `$HOME` (`~/.claude/skills/` and `~/.agents/skills/`):

```bash
curl -fsSL https://raw.githubusercontent.com/apokamo/show-and-grill/main/install.sh | sh -s -- --user --version v0.1.0
```

| Option | Meaning |
|---|---|
| `--user` | Install under `$HOME` instead of the current git repository |
| `--version vX.Y.Z` | The tag to install. Default: the latest `vX.Y.Z` tag |
| `--from <dir>` | Copy from a local checkout of this repository instead of downloading |

Run it again to update: it replaces the copy and the link with the same name. If it fails, the old copy stays as it was. If the skill is also installed in the other place (in the repository and for the user), it prints a warning and still installs.

### Windows without WSL

Copy the folder by hand. In PowerShell, from a checkout of this repository:

```powershell
# Claude Code, for the user
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
Copy-Item -Recurse -Force skills\show-and-grill "$HOME\.claude\skills\"

# Codex, for the user
New-Item -ItemType Directory -Force "$HOME\.agents\skills" | Out-Null
Copy-Item -Recurse -Force skills\show-and-grill "$HOME\.agents\skills\"
```

To install into one repository instead, use `<repository>\.claude\skills` and `<repository>\.agents\skills`. To update, delete the old `show-and-grill` folder first, then copy again.

The page server uses Python when it is installed. Without Python, the skill uses `scripts/serve.ps1` (Windows PowerShell 5.1 or PowerShell 7).

## Requirements

- A coding agent that reads skills: Claude Code or Codex.
- For the page server: Python 3 (`python3`, `python`, or `py -3`), or PowerShell on Windows.
- `gh` (GitHub CLI) to write decisions into an issue. Without it, the skill writes a Markdown file for you to paste.
- The pages load marked, DOMPurify, and web fonts from a CDN. Offline, the page shows the raw Markdown.

## Development

Run the tests (Python 3.12 or later):

```bash
python -m pip install pytest pyyaml
python -m pytest
```

The `serve.ps1` tests run when `pwsh` (or, on Windows, `powershell`) is found, and are skipped otherwise. Set `SHOW_AND_GRILL_REQUIRE_PWSH=1` to make each missing PowerShell fail instead (on Windows, both `powershell` and `pwsh` are then required; the CI does this). The `install.sh` tests do not run on Windows.

## Credits

This is a new implementation. No code was copied; only ideas were taken. The main sources:

- [mattpocock/skills](https://github.com/mattpocock/skills) (MIT): the grilling idea and its core rules.
- [nicobailon/visual-explainer](https://github.com/nicobailon/visual-explainer) (MIT): plain language and the figure parts.
- [jasonku09/grill-with-ui](https://github.com/jasonku09/grill-with-ui) (MIT): showing a recommendation next to each question.

See "Sources and credits" in [SKILL.md](skills/show-and-grill/SKILL.md) for the full list.

## License

[MIT](LICENSE)
