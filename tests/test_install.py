"""Tests for install.sh.

Each test makes an empty git repository, a temporary ``HOME``, and a temporary
``TMPDIR`` under ``tmp_path`` and runs ``sh install.sh`` in a child process.
Downloads use a local bare repository through ``SHOW_AND_GRILL_REPO_URL``.

install.sh targets macOS and Linux (including WSL). On those systems ``sh`` and
``git`` are required: a missing tool fails the tests instead of skipping them.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

if sys.platform == "win32":
    pytest.skip(
        "install.sh does not target Windows without WSL (the README gives the copy-by-hand steps)",
        allow_module_level=True,
    )

_REPO_DIR = Path(__file__).resolve().parents[1]
_INSTALL_SH = _REPO_DIR / "install.sh"
_SKILL_SRC = _REPO_DIR / "skills" / "show-and-grill"
_NAME = "show-and-grill"
_LINK_TARGET = "../../.claude/skills/show-and-grill"


def _require(tool: str) -> str:
    """Return the path of ``tool``, or fail the test when it is missing.

    Args:
        tool: The command name.

    Returns:
        The full path of the command.
    """
    path = shutil.which(tool)
    if path is None:
        pytest.fail(f"{tool} is required to test install.sh on this system, but it was not found")
    return path


@dataclass
class Env:
    """The places one install.sh run uses."""

    root: Path
    repo: Path
    home: Path
    tmpdir: Path
    env: dict[str, str]

    def run(
        self, *args: str, cwd: Path | None = None, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run ``sh install.sh <args>``.

        Args:
            *args: Arguments for install.sh.
            cwd: The working folder. Default: the test repository.
            extra_env: More environment variables.

        Returns:
            The result.
        """
        return subprocess.run(  # noqa: S603 - run our own install.sh
            [_require("sh"), str(_INSTALL_SH), *args],
            cwd=cwd or self.repo,
            env={**self.env, **(extra_env or {})},
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    def git(self, *args: str, cwd: Path) -> str:
        """Run git with the test environment.

        Args:
            *args: Arguments for git.
            cwd: The working folder.

        Returns:
            Standard output.
        """
        result = subprocess.run(  # noqa: S603 - git in a test folder
            [_require("git"), *args],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return result.stdout


@pytest.fixture
def env(tmp_path: Path) -> Env:
    """Make an empty git repository, a temporary HOME and TMPDIR, and a clean git setup.

    Returns:
        The test environment.
    """
    _require("sh")
    _require("git")
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    tmpdir = tmp_path / "tmpdir"
    for folder in (repo, home, tmpdir):
        folder.mkdir()
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    variables = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "TMPDIR": str(tmpdir),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": str(gitconfig),
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
    result = Env(root=tmp_path, repo=repo, home=home, tmpdir=tmpdir, env=variables)
    result.git("init", "-q", cwd=repo)
    return result


def _relative_files(base: Path) -> list[str]:
    """List the files under ``base`` as relative paths, without ``__pycache__``.

    Args:
        base: The folder.

    Returns:
        Sorted relative paths.
    """
    return sorted(
        str(p.relative_to(base))
        for p in base.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def _temp_names(*folders: Path) -> list[str]:
    """List leftover temporary names (``.show-and-grill.*``) in ``folders``.

    Args:
        *folders: Folders to look in.

    Returns:
        The names found.
    """
    return sorted(
        p.name
        for folder in folders
        if folder.is_dir()
        for p in folder.iterdir()
        if p.name.startswith(f".{_NAME}.")
    )


def _assert_installed(root: Path) -> None:
    """Check the copy and the link under ``root``.

    Args:
        root: The repository or the HOME folder.
    """
    dest = root / ".claude" / "skills" / _NAME
    link = root / ".agents" / "skills" / _NAME
    assert (dest / "SKILL.md").is_file()
    assert link.is_symlink()
    assert os.readlink(link) == _LINK_TARGET
    assert (link / "SKILL.md").is_file()
    assert not any(p.name == "__pycache__" for p in dest.rglob("*"))
    assert _relative_files(dest) == _relative_files(_SKILL_SRC)
    assert _temp_names(dest.parent, link.parent) == []


# --- local copy (--from) ---------------------------------------------------------


def test_installs_into_the_repository(env: Env) -> None:
    """By default, the skill goes into the current git repository, with a relative link for Codex."""
    result = env.run("--from", str(_REPO_DIR))
    assert result.returncode == 0, result.stderr
    _assert_installed(env.repo)
    assert "warning:" not in result.stderr
    assert f"local {_REPO_DIR}" in result.stdout
    assert str(env.repo / ".claude" / "skills" / _NAME) in result.stdout
    assert not (env.home / ".claude").exists()


def test_installs_from_a_subfolder_of_the_repository(env: Env) -> None:
    """Run from a subfolder, the skill goes to the repository top."""
    sub = env.repo / "a" / "b"
    sub.mkdir(parents=True)
    result = env.run("--from", str(_REPO_DIR), cwd=sub)
    assert result.returncode == 0, result.stderr
    _assert_installed(env.repo)


def test_copy_has_normal_folder_mode(env: Env) -> None:
    """The copied folder is not private (mktemp makes 0700; the copy follows umask)."""
    result = env.run("--from", str(_REPO_DIR))
    assert result.returncode == 0, result.stderr
    mode = (env.repo / ".claude" / "skills" / _NAME).stat().st_mode & 0o777
    assert mode & 0o055 == 0o055, oct(mode)


def test_installs_for_the_user(env: Env) -> None:
    """``--user`` installs under HOME and not into the repository."""
    result = env.run("--user", "--from", str(_REPO_DIR))
    assert result.returncode == 0, result.stderr
    _assert_installed(env.home)
    assert not (env.repo / ".claude").exists()
    assert not (env.repo / ".agents").exists()


def test_user_install_works_outside_a_repository(env: Env) -> None:
    """``--user`` does not need a git repository."""
    outside = env.root / "outside"
    outside.mkdir()
    result = env.run("--user", "--from", str(_REPO_DIR), cwd=outside)
    assert result.returncode == 0, result.stderr
    _assert_installed(env.home)


def test_replaces_an_old_copy_and_link(env: Env) -> None:
    """An old folder (with an extra file) and an old link are replaced."""
    dest = env.repo / ".claude" / "skills" / _NAME
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("old\n", encoding="utf-8")
    (dest / "extra.txt").write_text("extra\n", encoding="utf-8")
    link = env.repo / ".agents" / "skills" / _NAME
    link.parent.mkdir(parents=True)
    link.symlink_to("../../somewhere-else")
    result = env.run("--from", str(_REPO_DIR))
    assert result.returncode == 0, result.stderr
    _assert_installed(env.repo)
    assert not (dest / "extra.txt").exists()
    assert (dest / "SKILL.md").read_text(encoding="utf-8") != "old\n"


def test_replaces_a_folder_where_the_link_goes(env: Env) -> None:
    """A real folder at the link path (for example, a copy made by hand) is replaced by the link."""
    link = env.repo / ".agents" / "skills" / _NAME
    link.mkdir(parents=True)
    (link / "SKILL.md").write_text("old\n", encoding="utf-8")
    result = env.run("--from", str(_REPO_DIR))
    assert result.returncode == 0, result.stderr
    _assert_installed(env.repo)


# --- warning when the skill is in the other place ---------------------------------


def _place(root: Path, where: str) -> None:
    """Put a skill folder at ``root/<where>/skills/show-and-grill``.

    Args:
        root: The repository or HOME.
        where: ``.claude`` or ``.agents``.
    """
    folder = root / where / "skills" / _NAME
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("other\n", encoding="utf-8")


@pytest.mark.parametrize("where", [".claude", ".agents"])
def test_warns_when_the_user_also_has_it(env: Env, where: str) -> None:
    """Installing into the repository warns when HOME also has the skill, and still installs."""
    _place(env.home, where)
    result = env.run("--from", str(_REPO_DIR))
    assert result.returncode == 0, result.stderr
    assert "warning:" in result.stderr
    assert str(env.home / where / "skills" / _NAME) in result.stderr
    _assert_installed(env.repo)


@pytest.mark.parametrize("where", [".claude", ".agents"])
def test_warns_when_the_repository_also_has_it(env: Env, where: str) -> None:
    """``--user`` warns when the current repository also has the skill, and still installs."""
    _place(env.repo, where)
    result = env.run("--user", "--from", str(_REPO_DIR))
    assert result.returncode == 0, result.stderr
    assert "warning:" in result.stderr
    _assert_installed(env.home)


def test_no_warning_when_home_is_the_repository(env: Env) -> None:
    """No warning when HOME is the repository top (both places are the same)."""
    result = env.run("--from", str(_REPO_DIR), extra_env={"HOME": str(env.repo)})
    assert result.returncode == 0, result.stderr
    result = env.run("--from", str(_REPO_DIR), extra_env={"HOME": str(env.repo)})
    assert result.returncode == 0, result.stderr
    assert "warning:" not in result.stderr
    _assert_installed(env.repo)


# --- usage errors and failures ------------------------------------------------------


def test_fails_outside_a_repository(env: Env) -> None:
    """The default mode outside a git repository exits 1 and makes nothing."""
    outside = env.root / "outside"
    outside.mkdir()
    result = env.run("--from", str(_REPO_DIR), cwd=outside)
    assert result.returncode == 1
    assert "--user" in result.stderr
    assert list(outside.iterdir()) == []
    assert list(env.home.iterdir()) == []


@pytest.mark.parametrize(
    "args",
    [
        ["--bogus"],
        ["--version"],
        ["--from"],
        ["--version", "v0.1.0", "--from", "."],
        ["--version", "0.1.0"],
        ["--version", "v1.2"],
        ["--version", "v1.2.3-rc1"],
        ["--version", "main"],
    ],
    ids=["unknown", "version-no-value", "from-no-value", "version-and-from", "no-v", "two-parts", "suffix", "branch"],
)
def test_usage_errors_exit_2(env: Env, args: list[str]) -> None:
    """Wrong usage exits 2 and makes nothing."""
    result = env.run(*args)
    assert result.returncode == 2, result.stderr
    assert not (env.repo / ".claude").exists()


def test_help_exits_0(env: Env) -> None:
    """``--help`` prints the usage."""
    result = env.run("--help")
    assert result.returncode == 0
    assert "--user" in result.stdout


def test_from_without_skill_fails_and_keeps_the_old_copy(env: Env) -> None:
    """``--from`` a folder without the skill exits 1 and leaves the old copy."""
    dest = env.repo / ".claude" / "skills" / _NAME
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("old\n", encoding="utf-8")
    empty = env.root / "empty"
    empty.mkdir()
    result = env.run("--from", str(empty))
    assert result.returncode == 1
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "old\n"


# --- downloading a tag ---------------------------------------------------------------


def _make_remote(env: Env, versions: dict[str, str]) -> Path:
    """Make a bare repository with one commit and one tag per version.

    Args:
        env: The test environment.
        versions: Tag name to the marker text written into SKILL.md.

    Returns:
        The bare repository path.
    """
    work = env.root / "remote-work"
    bare = env.root / "remote.git"
    work.mkdir()
    env.git("init", "-q", cwd=work)
    for tag, marker in versions.items():
        _write_skill(work, marker)
        env.git("add", "-A", cwd=work)
        env.git("commit", "-q", "-m", f"release {tag}", cwd=work)
        env.git("tag", tag, cwd=work)
    env.git("clone", "-q", "--bare", str(work), str(bare), cwd=env.root)
    return bare


def _write_skill(work: Path, marker: str) -> None:
    """Write a copy of the skill with ``marker`` at the end of SKILL.md.

    Args:
        work: The working tree.
        marker: Text that tells the versions apart.
    """
    skill = work / "skills" / _NAME
    if skill.exists():
        shutil.rmtree(skill)
    shutil.copytree(_SKILL_SRC, skill, ignore=shutil.ignore_patterns("__pycache__"))
    with (skill / "SKILL.md").open("a", encoding="utf-8") as fh:
        fh.write(f"\n<!-- {marker} -->\n")


def _installed_marker(root: Path) -> str:
    """Read the marker line at the end of the installed SKILL.md.

    Args:
        root: The repository or HOME.

    Returns:
        The last line.
    """
    text = (root / ".claude" / "skills" / _NAME / "SKILL.md").read_text(encoding="utf-8")
    return text.strip().splitlines()[-1]


def test_installs_the_latest_tag_by_number(env: Env) -> None:
    """Without ``--version``, the largest vX.Y.Z by number is installed (v0.10.0 > v0.2.0)."""
    bare = _make_remote(env, {"v0.1.0": "M-010", "v0.10.0": "M-0100", "v0.2.0": "M-020"})
    result = env.run(extra_env={"SHOW_AND_GRILL_REPO_URL": str(bare)})
    assert result.returncode == 0, result.stderr
    assert _installed_marker(env.repo) == "<!-- M-0100 -->"
    assert "v0.10.0" in result.stdout
    _assert_marker_install(env)


def _assert_marker_install(env: Env) -> None:
    """Check the link and that nothing is left in TMPDIR.

    Args:
        env: The test environment.
    """
    link = env.repo / ".agents" / "skills" / _NAME
    assert os.readlink(link) == _LINK_TARGET
    assert list(env.tmpdir.iterdir()) == []


def test_installs_the_given_tag(env: Env) -> None:
    """``--version v0.1.0`` installs that tag."""
    bare = _make_remote(env, {"v0.1.0": "M-010", "v0.2.0": "M-020"})
    result = env.run("--version", "v0.1.0", extra_env={"SHOW_AND_GRILL_REPO_URL": str(bare)})
    assert result.returncode == 0, result.stderr
    assert _installed_marker(env.repo) == "<!-- M-010 -->"
    _assert_marker_install(env)


def test_missing_tag_fails(env: Env) -> None:
    """A tag that does not exist exits 1 and installs nothing."""
    bare = _make_remote(env, {"v0.1.0": "M-010"})
    result = env.run("--version", "v0.9.0", extra_env={"SHOW_AND_GRILL_REPO_URL": str(bare)})
    assert result.returncode == 1
    assert not (env.repo / ".claude").exists()
    assert list(env.tmpdir.iterdir()) == []


def test_repository_without_tags_fails(env: Env) -> None:
    """A repository with no vX.Y.Z tag exits 1."""
    bare = _make_remote(env, {})
    work = env.root / "remote-work"
    _write_skill(work, "M-none")
    env.git("add", "-A", cwd=work)
    env.git("commit", "-q", "-m", "no tag", cwd=work)
    env.git("push", "-q", str(bare), "HEAD:refs/heads/main", cwd=work)
    result = env.run(extra_env={"SHOW_AND_GRILL_REPO_URL": str(bare)})
    assert result.returncode == 1
    assert not (env.repo / ".claude").exists()


def test_branch_without_tag_is_not_used(env: Env) -> None:
    """A branch named ``v9.9.9`` with no such tag is not installed."""
    bare = _make_remote(env, {"v0.1.0": "M-010"})
    work = env.root / "remote-work"
    _write_skill(work, "M-branch")
    env.git("add", "-A", cwd=work)
    env.git("commit", "-q", "-m", "branch", cwd=work)
    env.git("push", "-q", str(bare), "HEAD:refs/heads/v9.9.9", cwd=work)
    result = env.run("--version", "v9.9.9", extra_env={"SHOW_AND_GRILL_REPO_URL": str(bare)})
    assert result.returncode == 1
    assert not (env.repo / ".claude").exists()


def test_tag_wins_over_a_branch_with_the_same_name(env: Env) -> None:
    """With a tag and a branch both named ``v0.1.0``, the tag's content is installed."""
    bare = _make_remote(env, {"v0.1.0": "M-tag"})
    work = env.root / "remote-work"
    _write_skill(work, "M-branch")
    env.git("add", "-A", cwd=work)
    env.git("commit", "-q", "-m", "branch", cwd=work)
    env.git("push", "-q", str(bare), "HEAD:refs/heads/v0.1.0", cwd=work)
    result = env.run("--version", "v0.1.0", extra_env={"SHOW_AND_GRILL_REPO_URL": str(bare)})
    assert result.returncode == 0, result.stderr
    assert _installed_marker(env.repo) == "<!-- M-tag -->"


def test_annotated_tag_installs_its_commit(env: Env) -> None:
    """An annotated tag installs the commit it points to."""
    bare = _make_remote(env, {"v0.1.0": "M-010"})
    work = env.root / "remote-work"
    _write_skill(work, "M-annotated")
    env.git("add", "-A", cwd=work)
    env.git("commit", "-q", "-m", "annotated", cwd=work)
    env.git("tag", "-a", "v0.3.0", "-m", "v0.3.0", cwd=work)
    env.git("push", "-q", str(bare), "refs/tags/v0.3.0", cwd=work)
    result = env.run(extra_env={"SHOW_AND_GRILL_REPO_URL": str(bare)})
    assert result.returncode == 0, result.stderr
    assert _installed_marker(env.repo) == "<!-- M-annotated -->"
    result = env.run("--version", "v0.3.0", extra_env={"SHOW_AND_GRILL_REPO_URL": str(bare)})
    assert result.returncode == 0, result.stderr
    assert _installed_marker(env.repo) == "<!-- M-annotated -->"


# --- a failure in the middle keeps the old copy ---------------------------------------


_FAKE_TOOLS = {
    "cp-partial": (
        "cp",
        '"$REAL" "$@"\nexit 1\n',
    ),
    "ln-fails": ("ln", "exit 1\n"),
    "mv-new-copy-fails": (
        "mv",
        'case "$(basename -- "$1")" in .show-and-grill.new.*) exit 1 ;; esac\nexec "$REAL" "$@"\n',
    ),
    "mv-new-link-fails": (
        "mv",
        'case "$(basename -- "$1")" in .show-and-grill.link.*) exit 1 ;; esac\nexec "$REAL" "$@"\n',
    ),
}


@pytest.mark.parametrize("fault", sorted(_FAKE_TOOLS))
def test_failure_in_the_middle_keeps_the_old_copy(env: Env, fault: str) -> None:
    """When cp, ln, or a swap mv fails, the old copy and link stay as they were and no temporary name is left."""
    dest = env.repo / ".claude" / "skills" / _NAME
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("OLD-MARKER\n", encoding="utf-8")
    link = env.repo / ".agents" / "skills" / _NAME
    link.parent.mkdir(parents=True)
    link.symlink_to("../../old-target")

    tool, body = _FAKE_TOOLS[fault]
    real = _require(tool)
    shims = env.root / "bin"
    shims.mkdir()
    shim = shims / tool
    shim.write_text(f'#!/bin/sh\nREAL="{real}"\n{body}', encoding="utf-8")
    shim.chmod(0o755)

    result = env.run(
        "--from",
        str(_REPO_DIR),
        extra_env={"PATH": f"{shims}{os.pathsep}{env.env['PATH']}"},
    )
    assert result.returncode != 0
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "OLD-MARKER\n"
    assert sorted(p.name for p in dest.iterdir()) == ["SKILL.md"]
    assert link.is_symlink()
    assert os.readlink(link) == "../../old-target"
    assert _temp_names(dest.parent, link.parent) == []


# --- a signal in the middle of the swap keeps the old copy ----------------------------


# Each shim sends a signal to install.sh (its parent) just before a swap step.
_SIGNAL_POINTS = {
    "before-new-copy": ".show-and-grill.new.*",
    "before-new-link": ".show-and-grill.link.*",
}


def _old_install(env: Env) -> tuple[Path, Path]:
    """Place an old copy and an old link in the repository.

    Args:
        env: The test environment.

    Returns:
        The copy folder and the link.
    """
    dest = env.repo / ".claude" / "skills" / _NAME
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("OLD-MARKER\n", encoding="utf-8")
    link = env.repo / ".agents" / "skills" / _NAME
    link.parent.mkdir(parents=True)
    link.symlink_to("../../old-target")
    return dest, link


def _mv_shim(env: Env, body: str) -> dict[str, str]:
    """Put a fake ``mv`` first on PATH.

    Args:
        env: The test environment.
        body: The shell lines run before the real ``mv`` (``$REAL``).

    Returns:
        The extra environment for ``env.run``.
    """
    real = _require("mv")
    shims = env.root / "bin"
    shims.mkdir()
    shim = shims / "mv"
    shim.write_text(f'#!/bin/sh\nREAL="{real}"\n{body}exec "$REAL" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)
    return {"PATH": f"{shims}{os.pathsep}{env.env['PATH']}"}


@pytest.mark.parametrize("signal_name", ["TERM", "INT", "HUP"])
@pytest.mark.parametrize("point", sorted(_SIGNAL_POINTS))
def test_signal_in_the_middle_keeps_the_old_copy(env: Env, point: str, signal_name: str) -> None:
    """A signal after the old copy is moved aside puts the old copy and link back."""
    dest, link = _old_install(env)
    pattern = _SIGNAL_POINTS[point]
    extra_env = _mv_shim(
        env,
        f'case "$(basename -- "$1")" in {pattern}) kill -{signal_name} "$PPID"; exit 1 ;; esac\n',
    )

    result = env.run("--from", str(_REPO_DIR), extra_env=extra_env)
    assert result.returncode == 1
    assert "interrupted" in result.stderr
    assert (dest / "SKILL.md").read_text(encoding="utf-8") == "OLD-MARKER\n"
    assert sorted(p.name for p in dest.iterdir()) == ["SKILL.md"]
    assert link.is_symlink()
    assert os.readlink(link) == "../../old-target"
    assert _temp_names(dest.parent, link.parent) == []


def test_signal_in_the_middle_of_a_first_install_leaves_nothing(env: Env) -> None:
    """A signal during a first install removes the half-placed copy and leaves no temporary name."""
    extra_env = _mv_shim(
        env,
        'case "$(basename -- "$1")" in .show-and-grill.link.*) kill -TERM "$PPID"; exit 1 ;; esac\n',
    )

    result = env.run("--from", str(_REPO_DIR), extra_env=extra_env)
    assert result.returncode == 1
    dest = env.repo / ".claude" / "skills" / _NAME
    link = env.repo / ".agents" / "skills" / _NAME
    assert not dest.exists()
    assert not link.is_symlink()
    assert _temp_names(dest.parent, link.parent) == []


def test_signal_with_a_failed_restore_keeps_the_backup(env: Env) -> None:
    """When the old copy cannot be moved back, it is kept under its backup name and the path is shown."""
    dest, link = _old_install(env)
    extra_env = _mv_shim(
        env,
        'case "$(basename -- "$1")" in\n'
        '    .show-and-grill.link.*) kill -TERM "$PPID"; exit 1 ;;\n'
        "    .show-and-grill.old.*) exit 1 ;;\n"
        "esac\n",
    )

    result = env.run("--from", str(_REPO_DIR), extra_env=extra_env)
    assert result.returncode == 1
    backups = [p for p in dest.parent.iterdir() if p.name.startswith(f".{_NAME}.old.")]
    assert len(backups) == 1
    assert (backups[0] / "SKILL.md").read_text(encoding="utf-8") == "OLD-MARKER\n"
    assert f"the old copy is at {backups[0]}" in result.stderr
    assert link.is_symlink()
    assert os.readlink(link) == "../../old-target"
