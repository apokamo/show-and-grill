"""Check that show-and-grill is a stand-alone skill and that the repository is ready to share.

- No strings from the repository where the skill was first written
- The SKILL.md frontmatter has only ``name`` and ``description``
- Two example pages (English and Japanese) and a sequence figure (``lifeline``)
- ``grill.css`` has the figure parts, and ``grill.js`` has the light / dark toggle
- SKILL.md: how pages open, stopping and resuming, the PowerShell server
- The plugin files, LICENSE, README, and ``serve.ps1`` encoding
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml

_REPO_DIR = Path(__file__).resolve().parents[1]
_SKILL_DIR = _REPO_DIR / "skills" / "show-and-grill"

# Strings from the repository where the skill was first written. The pattern is
# built from pieces so that this file does not match itself.
_ORIGIN_PATTERN = re.compile(
    "|".join(
        (
            "apokamo/" + "kamo2",
            "uv run " + "kaji",
            "tmp/" + "kaji",
            "_shared" + "/",
            "issue-review-" + "ready",
            "#" + r"[0-9]{3,}\b",
        )
    ),
    re.IGNORECASE,
)
_MERMAID_PATTERN = re.compile("mer" + "maid", re.IGNORECASE)

# Figure parts that grill.css must define
_NEW_PARTS = (
    "lifeline",
    "store",
    "key",
    "area-name",
    "callout",
    "dim",
    "added",
    "removed",
    "cases",
)

_SKIP_PARTS = {"__pycache__", ".pytest_cache", ".git"}

# Suffixes that ``_hits`` does not read: CSS (colors look like issue numbers) and images.
_NOT_SEARCHED = {".css", ".png"}


def _files(base: Path) -> list[Path]:
    """List the files under ``base``, without caches and git data.

    Args:
        base: The folder to list.

    Returns:
        File paths, sorted by name.
    """
    return sorted(
        p for p in base.rglob("*") if p.is_file() and not _SKIP_PARTS & set(p.parts)
    )


def _text_files() -> list[Path]:
    """List the files in the skill folder, without ``__pycache__``.

    Returns:
        File paths, sorted by name.
    """
    return _files(_SKILL_DIR)


def _hits(base: Path, pattern: re.Pattern[str]) -> list[str]:
    """Find lines that match ``pattern`` in text files under ``base`` (``_NOT_SEARCHED`` excluded).

    Args:
        base: The folder to search.
        pattern: The pattern.

    Returns:
        ``path:line: text`` for each match.
    """
    return [
        f"{path.relative_to(base)}:{lineno}: {line.strip()}"
        for path in _files(base)
        if path.suffix not in _NOT_SEARCHED
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(), start=1
        )
        if pattern.search(line)
    ]


def _skill_md() -> str:
    """Read SKILL.md.

    Returns:
        The whole text.
    """
    return (_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict[str, object]:
    """Read the frontmatter at the top of SKILL.md.

    Args:
        text: The whole SKILL.md.

    Returns:
        The frontmatter.
    """
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md has no frontmatter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict)
    return data


def test_skill_dir_exists() -> None:
    """The scanned folder is not empty."""
    assert (_SKILL_DIR / "SKILL.md").is_file()
    assert len(_text_files()) >= 7


def test_no_origin_specific_strings() -> None:
    """No origin repository rules, issue numbers, or Mermaid in the skill (``*.css`` excluded)."""
    assert _hits(_SKILL_DIR, _ORIGIN_PATTERN) == []
    assert _hits(_SKILL_DIR, _MERMAID_PATTERN) == []


def test_frontmatter_has_only_name_and_description() -> None:
    """The frontmatter keys are only name and description, and name equals the folder name."""
    data = _frontmatter(_skill_md())
    assert set(data) == {"name", "description"}
    assert data["name"] == _SKILL_DIR.name


def test_examples_exist_in_english_and_japanese() -> None:
    """There are English and Japanese example pages, and at least one has a sequence figure."""
    langs: dict[str, str] = {}
    for name in ("example-en.html", "example-ja.html"):
        text = (_SKILL_DIR / name).read_text(encoding="utf-8")
        match = re.search(r'<html lang="([a-z-]+)"', text)
        assert match, name
        langs[name] = text
        assert match.group(1) == name.removeprefix("example-").removesuffix(".html")
    assert any('class="lifeline"' in text for text in langs.values())


def test_css_has_new_figure_parts() -> None:
    """grill.css defines the figure part classes."""
    css = (_SKILL_DIR / "assets" / "grill.css").read_text(encoding="utf-8")
    missing = [part for part in _NEW_PARTS if not re.search(rf"\.{part}\b", css)]
    assert missing == []


def test_theme_toggle() -> None:
    """grill.js shows a light / dark toggle with English and Japanese labels and remembers the choice."""
    js = (_SKILL_DIR / "assets" / "grill.js").read_text(encoding="utf-8")
    css = (_SKILL_DIR / "assets" / "grill.css").read_text(encoding="utf-8")
    assert 'id = "theme-toggle"' in js
    assert js.count("toDark:") == 2 and js.count("toLight:") == 2  # en and ja
    assert "dataset.theme" in js
    assert "localStorage" in js
    assert "#theme-toggle" in css


def test_dashes_only_for_unchecked() -> None:
    """Dashes (``stroke-dasharray``) are only on ``dashed`` classes (a dash means "not checked in the code")."""
    css = (_SKILL_DIR / "assets" / "grill.css").read_text(encoding="utf-8")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    offenders = [
        selector.strip()
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
        if "stroke-dasharray" in body
        for selector in selectors.split(",")
        if "dashed" not in selector
    ]
    assert offenders == []


def _section(text: str, heading: str) -> str:
    """Cut the section ``heading`` from SKILL.md (up to the next heading of the same or higher level).

    Args:
        text: The whole SKILL.md.
        heading: The start of the heading line, with ``#`` (``### Step 3: ...``).

    Returns:
        The section text.
    """
    level = len(heading) - len(heading.lstrip("#"))
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(heading))
    end = len(lines)
    for i in range(start + 1, len(lines)):
        match = re.match(r"^(#+) ", lines[i])
        if match and len(match.group(1)) <= level:
            end = i
            break
    return "\n".join(lines[start:end])


def test_server_steps_take_over_on_resume() -> None:
    """Step 3 starts the server on every resume, takes over, and gets the URL from this start's ``SERVING`` line.

    Step 6 gives the ``serve.py`` command to open the pages again later.
    """
    text = _skill_md()
    step3 = _section(text, "### Step 3:")
    assert "--wait-log" in step3
    assert "SERVING" in step3
    assert re.search(r"always start the server", step3, re.IGNORECASE)
    assert re.search(r"resume", step3, re.IGNORECASE)
    assert re.search(r"takes? over", step3, re.IGNORECASE)
    assert "if a server already runs, you get the same URL" not in step3
    assert re.search(r"do not use `--status`", step3, re.IGNORECASE)
    step6 = _section(text, "### Step 6:")
    assert "scripts/serve.py" in step6


def _claude_code_launch_command(text: str) -> str:
    """Get the Claude Code (WSL and Linux) server start command from Step 3.

    Args:
        text: The whole SKILL.md.

    Returns:
        The start command in backticks (starts with ``rm -f`` and has ``setsid nohup``).
    """
    step3 = _section(text, "### Step 3:")
    commands = re.findall(r"`(rm -f [^`\n]*setsid nohup[^`\n]*)`", step3)
    assert len(commands) == 1, commands
    return commands[0]


@pytest.mark.skipif(
    not all(shutil.which(c) for c in ("bash", "setsid", "nohup", "rm", "sleep")),
    reason="needs bash, setsid, and nohup (the WSL and Linux start command)",
)
def test_claude_code_launch_removes_old_log_before_returning(tmp_path: Path) -> None:
    """The start command returns only after the old ``.serve.log`` is removed, and puts only the server in the background.

    Even with a slow ``rm``, the old server's ``SERVING`` line is gone when the command
    returns (otherwise the next ``--wait-log`` would take the old server's URL).
    """
    command = _claude_code_launch_command(_skill_md())
    grill = tmp_path / "grill"
    grill.mkdir()
    stale = "SERVING http://127.0.0.1:45737/ (idle stop: 120 min)\n"
    (grill / ".serve.log").write_text(stale, encoding="utf-8")

    real_rm = shutil.which("rm")
    real_sleep = shutil.which("sleep")
    shims = tmp_path / "bin"
    shims.mkdir()
    # Slow down rm. python3 does not start a server: it waits a little and ends without output
    (shims / "rm").write_text(
        f'#!/bin/sh\n"{real_sleep}" 1\nexec "{real_rm}" "$@"\n', encoding="utf-8"
    )
    (shims / "python3").write_text(f'#!/bin/sh\nexec "{real_sleep}" 2\n', encoding="utf-8")
    for shim in shims.iterdir():
        shim.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{shims}{os.pathsep}{os.environ.get('PATH', '')}",
        "GRILL_DIR": str(grill),
        "SKILL_DIR": str(tmp_path / "skill"),
    }
    subprocess.run(  # noqa: S603 - run the SKILL.md start command with bash
        [shutil.which("bash") or "bash", "-c", command],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=True,
    )
    log = grill / ".serve.log"
    remaining = log.read_text(encoding="utf-8") if log.exists() else ""
    assert "45737" not in remaining, "the old SERVING line is still there when the command returns"


# --- SKILL.md: how pages open, stopping and resuming, the PowerShell server ----


def test_pages_open_from_a_link() -> None:
    """Pages open from a printed link; no per-OS open commands or extra permissions."""
    text = _skill_md()
    for word in ("explorer.exe", "xdg-open", "@Browser", "extra run permission"):
        assert word not in text, word
    assert re.search(r"print the link in the terminal", text, re.IGNORECASE)
    assert re.search(
        r"permission settings allow it, you may also open the page automatically",
        text,
        re.IGNORECASE,
    )
    description = str(_frontmatter(text)["description"])
    assert "opens in the browser" not in description
    assert "link" in description


def test_stopping_and_resuming() -> None:
    """SKILL.md says how to save progress, what not to write, and where to resume."""
    section = _section(_skill_md(), "### Stopping and resuming")
    assert "## show-and-grill progress" in section
    assert re.search(r"comment", section, re.IGNORECASE)
    assert "GRILL_DIR" in section
    assert re.search(r"do not rewrite the issue body", section, re.IGNORECASE)
    assert re.search(r"provenance.*only at the end", section, re.IGNORECASE)
    assert re.search(r"continue from the first open question", section, re.IGNORECASE)


def test_powershell_server_is_described() -> None:
    """SKILL.md says to use ``scripts/serve.ps1`` on Windows when no Python is found."""
    text = _skill_md()
    assert "scripts/serve.ps1" in text
    step3 = _section(text, "### Step 3:")
    assert "serve.ps1" in step3
    assert re.search(r"Windows without Python", step3)
    assert re.search(r"none of `python3`, `python`, or `py -3` is found", step3)


# --- The repository: no origin strings, plugins, license, README, encoding -----


def test_repository_has_no_origin_specific_strings() -> None:
    """No origin repository strings anywhere in the repository (``*.css``, images, and caches excluded)."""
    assert _hits(_REPO_DIR, _ORIGIN_PATTERN) == []


def test_license_is_mit() -> None:
    """LICENSE is the MIT License."""
    text = (_REPO_DIR / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License")
    assert "Permission is hereby granted, free of charge" in text


def _json(relative: str) -> dict[str, object]:
    """Read a JSON file in the repository.

    Args:
        relative: The path from the repository root.

    Returns:
        The parsed object.
    """
    data = json.loads((_REPO_DIR / relative).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_claude_code_plugin() -> None:
    """The Claude Code plugin and marketplace use the repository root as the plugin."""
    plugin = _json(".claude-plugin/plugin.json")
    assert plugin["name"] == "show-and-grill"
    assert plugin["license"] == "MIT"
    assert "skills" not in plugin  # skills/ is the default
    market = _json(".claude-plugin/marketplace.json")
    assert market["name"] == "show-and-grill"
    assert "owner" in market
    plugins = market["plugins"]
    assert isinstance(plugins, list) and len(plugins) == 1
    assert plugins[0]["name"] == "show-and-grill"
    assert plugins[0]["source"] == "./"


def test_codex_plugin() -> None:
    """The Codex plugin points to ``./skills/``, and its marketplace has the official metadata."""
    plugin = _json(".codex-plugin/plugin.json")
    assert plugin["name"] == "show-and-grill"
    assert plugin["skills"] == "./skills/"
    market = _json(".agents/plugins/marketplace.json")
    assert market["name"] == "show-and-grill"
    plugins = market["plugins"]
    assert isinstance(plugins, list) and len(plugins) == 1
    entry = plugins[0]
    assert entry["name"] == "show-and-grill"
    assert entry["source"] == {"source": "local", "path": "./"}
    assert entry["policy"]["installation"] in {"AVAILABLE", "INSTALLED_BY_DEFAULT", "NOT_AVAILABLE"}
    assert entry["policy"]["authentication"] in {"ON_INSTALL", "ON_USE"}
    assert isinstance(entry["category"], str) and entry["category"]


def test_skill_folder_is_found_by_tools() -> None:
    """Every tool finds the skill at ``skills/<name>/SKILL.md``."""
    assert (_REPO_DIR / "skills" / "show-and-grill" / "SKILL.md").is_file()
    assert sorted(p.name for p in (_REPO_DIR / "skills").iterdir()) == ["show-and-grill"]


def test_readme_covers_install_and_credits() -> None:
    """README has the three ways to install, ``--user``, Windows by hand, "pick one", and the sources."""
    text = (_REPO_DIR / "README.md").read_text(encoding="utf-8")
    for needle in (
        "claude plugin marketplace add apokamo/show-and-grill",
        "claude plugin install show-and-grill@show-and-grill",
        "codex plugin marketplace add apokamo/show-and-grill",
        "npx skills add apokamo/show-and-grill",
        "install.sh",
        "--user",
        "Windows without WSL",
        "Copy-Item",
        "Pick one way per agent",
        "https://github.com/mattpocock/skills",
        "https://github.com/nicobailon/visual-explainer",
        "https://github.com/jasonku09/grill-with-ui",
        "[MIT](LICENSE)",
    ):
        assert needle in text, needle


_FENCE = re.compile(r"^```(\w*)\n(.*?)^```", re.MULTILINE | re.DOTALL)


def _commands(text: str) -> list[str]:
    """Return the ``bash`` and ``powershell`` blocks of a README, without comments.

    Comments are translated, so they are dropped; the commands must stay the same.

    Args:
        text: The README text.

    Returns:
        One string per block, in order.
    """
    blocks = []
    for lang, body in _FENCE.findall(text):
        if lang not in {"bash", "powershell"}:
            continue
        lines = [re.sub(r"\s+#.*$", "", line).rstrip() for line in body.splitlines()]
        blocks.append("\n".join(line for line in lines if line and not line.startswith("#")))
    return blocks


def _heading_levels(text: str) -> list[int]:
    """Return the level of each Markdown heading outside code blocks.

    Args:
        text: The README text.

    Returns:
        The number of ``#`` of each heading, in order.
    """
    outside = _FENCE.sub("", text)
    return [len(m.group(1)) for m in re.finditer(r"^(#{1,6}) ", outside, re.MULTILINE)]


def test_readme_has_both_languages() -> None:
    """``README.md`` (the source) and ``README.ja.md`` link to each other and match in structure.

    Both have the same commands (``bash`` and ``powershell`` blocks, comments aside) and
    the same heading outline. This does not check the translation itself.
    """
    en = (_REPO_DIR / "README.md").read_text(encoding="utf-8")
    ja = (_REPO_DIR / "README.ja.md").read_text(encoding="utf-8")
    assert "[日本語](README.ja.md)" in en
    assert "[English](README.md)" in ja
    assert len(_commands(en)) >= 6
    assert _commands(ja) == _commands(en)
    assert _heading_levels(ja) == _heading_levels(en)
    for needle in ("https://github.com/mattpocock/skills", "[MIT](LICENSE)"):
        assert needle in ja, needle


_PICTURE = re.compile(r"<picture>(.*?)</picture>", re.DOTALL)


@pytest.mark.parametrize(
    ("readme", "prefix"),
    [("README.md", "docs/images/example-"), ("README.ja.md", "docs/images/example-ja-")],
)
def test_readme_shows_the_example_page(readme: str, prefix: str) -> None:
    """Each README shows the example page in its language: dark in ``<source>``, light in ``<img>``."""
    text = (_REPO_DIR / readme).read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    pictures = _PICTURE.findall(text)
    assert len(pictures) == 1
    picture = pictures[0]
    assert re.search(
        rf'<source media="\(prefers-color-scheme: dark\)" srcset="{prefix}dark\.png">',
        picture,
    )
    assert re.search(rf'<img alt="[^"]+" src="{prefix}light\.png"', picture)
    for image in (f"{prefix}light.png", f"{prefix}dark.png"):
        assert (_REPO_DIR / image).is_file(), image


def test_changelog_has_the_plugin_version() -> None:
    """``CHANGELOG.md`` has an Unreleased section and a section for the plugin version."""
    text = (_REPO_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [Unreleased]" in text
    version = _json(".claude-plugin/plugin.json")["version"]
    assert _json(".codex-plugin/plugin.json")["version"] == version
    assert f"## [{version}] - " in text
    assert f"/releases/tag/v{version}" in text


def test_serve_ps1_has_bom_and_ascii_only() -> None:
    """``serve.ps1`` starts with a UTF-8 BOM and the rest is ASCII (Windows PowerShell 5.1 reads it right)."""
    data = (_SKILL_DIR / "scripts" / "serve.ps1").read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    rest = data[3:]
    assert all(b < 0x80 for b in rest)
    assert b"\xef\xbb\xbf" not in rest
