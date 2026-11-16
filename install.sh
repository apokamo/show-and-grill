#!/bin/sh
# Install the show-and-grill skill for Claude Code and Codex.
#
#   sh install.sh [--user] [--version <tag>] [--from <dir>] [--help]
#
# Default: copy into the current git repository:
#   <repo>/.claude/skills/show-and-grill/          (a copy of the skill folder)
#   <repo>/.agents/skills/show-and-grill           (a relative link to the copy, for Codex)
# --user: the same two paths under $HOME instead.
#
# An existing copy and link with the same name are replaced. A failure leaves
# them as they were.
set -u

DEFAULT_REPO_URL="https://github.com/apokamo/show-and-grill.git"
NAME="show-and-grill"
LINK_TARGET="../../.claude/skills/$NAME"

usage() {
    cat <<'USAGE_END'
Usage: sh install.sh [--user] [--version <tag>] [--from <dir>]

Install the show-and-grill skill for Claude Code and Codex.

  (no option)      Install into the current git repository:
                     .claude/skills/show-and-grill/  and  .agents/skills/show-and-grill (link)
  --user           Install for the user, under $HOME instead.
  --version <tag>  The version to install (vX.Y.Z). Default: the latest vX.Y.Z tag.
  --from <dir>     Copy from a local checkout of the show-and-grill repository
                   instead of downloading. Cannot be used with --version.
  --help           Show this help.

Environment:
  SHOW_AND_GRILL_REPO_URL  The git URL to download from
                           (default: https://github.com/apokamo/show-and-grill.git).

Exit codes: 0 installed, 1 failed, 2 wrong usage.
USAGE_END
}

die() {
    printf 'error: %s\n' "$1" >&2
    exit 1
}

usage_error() {
    printf 'error: %s\n' "$1" >&2
    printf 'Run "sh install.sh --help" for usage.\n' >&2
    exit 2
}

# ---- arguments ---------------------------------------------------------------

mode=repo
version=
from=
has_version=0
has_from=0
while [ "$#" -gt 0 ]; do
    case "$1" in
        --user)
            mode=user
            ;;
        --version)
            [ "$#" -ge 2 ] && [ -n "$2" ] || usage_error "--version needs a value"
            version=$2
            has_version=1
            shift
            ;;
        --version=*)
            version=${1#--version=}
            [ -n "$version" ] || usage_error "--version needs a value"
            has_version=1
            ;;
        --from)
            [ "$#" -ge 2 ] && [ -n "$2" ] || usage_error "--from needs a value"
            from=$2
            has_from=1
            shift
            ;;
        --from=*)
            from=${1#--from=}
            [ -n "$from" ] || usage_error "--from needs a value"
            has_from=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage_error "unknown argument: $1"
            ;;
    esac
    shift
done

if [ "$has_version" -eq 1 ] && [ "$has_from" -eq 1 ]; then
    usage_error "--version and --from cannot be used together"
fi
if [ "$has_version" -eq 1 ]; then
    if ! printf '%s\n' "$version" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$' \
        || [ "$(printf '%s\n' "$version" | wc -l)" -ne 1 ]; then
        usage_error "--version must look like vX.Y.Z (got: $version)"
    fi
fi

# ---- cleanup -----------------------------------------------------------------

work=
new_dir=
new_link=
keep_backup=0
backup_dir=
backup_link=
swapping=0
had_dest=0
had_link=0

# shellcheck disable=SC2329 # called by the EXIT trap
cleanup() {
    if [ -n "$work" ]; then rm -rf "$work"; fi
    if [ -n "$new_dir" ]; then rm -rf "$new_dir"; fi
    if [ -n "$new_link" ]; then rm -f "$new_link"; fi
    if [ "$keep_backup" -eq 0 ]; then
        if [ -n "$backup_dir" ]; then rm -rf "$backup_dir"; fi
        if [ -n "$backup_link" ]; then rm -rf "$backup_link"; fi
    fi
}
# on_signal: a signal in the middle of the swap puts the old copy and link
# back first; restore keeps them when it cannot.
# shellcheck disable=SC2329 # called by the signal trap
on_signal() {
    trap '' HUP INT TERM
    if [ "$swapping" -eq 1 ]; then restore; fi
    printf 'error: interrupted\n' >&2
    exit 1
}
trap cleanup EXIT
trap on_signal HUP INT TERM

# exists PATH: true for a file, a folder, or a link (even a broken link).
exists() {
    [ -e "$1" ] || [ -L "$1" ]
}

# ---- where to install --------------------------------------------------------

command -v git >/dev/null 2>&1 || die "git is not installed"

repo_top=$(git rev-parse --show-toplevel 2>/dev/null) || repo_top=
if [ "$mode" = user ]; then
    [ -n "${HOME:-}" ] || die "HOME is not set"
    root=$HOME
else
    [ -n "$repo_top" ] || die "not inside a git repository (use --user to install for the user)"
    root=$repo_top
fi
[ -n "$root" ] || die "the install folder is empty"

dest_parent=$root/.claude/skills
link_parent=$root/.agents/skills
dest=$dest_parent/$NAME
link=$link_parent/$NAME

# ---- where to copy from ------------------------------------------------------

if [ "$has_from" -eq 1 ]; then
    src=$from/skills/$NAME
    [ -f "$src/SKILL.md" ] || die "no skills/$NAME/SKILL.md in $from"
    label="local $from"
else
    url=${SHOW_AND_GRILL_REPO_URL:-$DEFAULT_REPO_URL}
    if [ "$has_version" -eq 0 ]; then
        tags=$(git ls-remote --tags --refs "$url" 2>/dev/null) \
            || die "cannot read the tags of $url"
        version=$(printf '%s\n' "$tags" \
            | sed -n 's|^[0-9a-f]*[[:space:]]*refs/tags/\(v[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)$|\1|p' \
            | sort -t. -k1.2,1n -k2,2n -k3,3n | tail -n 1)
        [ -n "$version" ] || die "no vX.Y.Z tag in $url"
    fi
    refs=$(git ls-remote --tags "$url" "refs/tags/$version" "refs/tags/$version^{}" 2>/dev/null) \
        || die "cannot read the tags of $url"
    tag_sha=$(printf '%s\n' "$refs" | awk -v r="refs/tags/$version" '$2 == r { print $1 }')
    peeled_sha=$(printf '%s\n' "$refs" | awk -v r="refs/tags/$version^{}" '$2 == r { print $1 }')
    [ -n "$tag_sha" ] || die "no tag $version in $url"
    want=${peeled_sha:-$tag_sha}

    work=$(mktemp -d) || die "cannot make a temporary folder"
    git init -q "$work/src" || die "git init failed"
    git -C "$work/src" fetch --quiet --depth 1 --no-tags "$url" \
        "+refs/tags/$version:refs/tags/$version" \
        || die "cannot download $version from $url"
    got=$(git -C "$work/src" rev-parse --verify --quiet "refs/tags/$version^{commit}") \
        || die "tag $version does not point to a commit"
    [ "$got" = "$want" ] || die "tag $version changed while downloading (expected $want, got $got)"
    git -C "$work/src" -c advice.detachedHead=false checkout --quiet "$got" \
        || die "cannot check out $version"
    src=$work/src/skills/$NAME
    [ -f "$src/SKILL.md" ] || die "no skills/$NAME/SKILL.md in $version"
    label=$version
fi

# ---- copy (prepare next to the old one, then swap) ---------------------------

mkdir -p "$dest_parent" "$link_parent" || die "cannot make $dest_parent or $link_parent"

new_dir=$(mktemp -d "$dest_parent/.$NAME.new.XXXXXX") || die "cannot make a folder in $dest_parent"
# mktemp -d makes the folder private (0700); give it the usual mode from umask.
chmod "$(umask -S)" "$new_dir" || die "cannot set the mode of the copy"
cp -R "$src/." "$new_dir/" || die "cannot copy the skill into $dest_parent"
find "$new_dir" -name __pycache__ -type d -prune -exec rm -rf {} + \
    || die "cannot remove __pycache__ from the copy"
[ -f "$new_dir/SKILL.md" ] || die "the copy has no SKILL.md"
new_link=$link_parent/.$NAME.link.$$
rm -f "$new_link"
ln -s "$LINK_TARGET" "$new_link" || die "cannot make a link in $link_parent"

# restore: put back what was moved aside, after a failed or interrupted swap.
# had_dest / had_link say what was there before the swap. A backup name that
# does not exist yet means the old one was never moved, so it is left alone.
restore() {
    trap '' HUP INT TERM
    swapping=0
    ok=1
    if [ "$had_dest" -eq 1 ]; then
        if [ -n "$backup_dir" ] && exists "$backup_dir"; then
            if exists "$dest"; then rm -rf "$dest" || ok=0; fi
            if [ "$ok" -eq 1 ] && mv "$backup_dir" "$dest"; then backup_dir=; else ok=0; fi
        fi
    elif exists "$dest"; then
        rm -rf "$dest" || ok=0
    fi
    if [ "$had_link" -eq 1 ]; then
        if [ -n "$backup_link" ] && exists "$backup_link"; then
            if exists "$link"; then rm -f "$link" || ok=0; fi
            if mv "$backup_link" "$link"; then backup_link=; else ok=0; fi
        fi
    elif exists "$link"; then
        rm -f "$link" || ok=0
    fi
    if [ "$ok" -eq 0 ]; then
        keep_backup=1
        [ -n "$backup_dir" ] && printf 'error: could not restore; the old copy is at %s\n' "$backup_dir" >&2
        [ -n "$backup_link" ] && printf 'error: could not restore; the old link is at %s\n' "$backup_link" >&2
    fi
    return 0
}

swap_failed() {
    restore
    die "$1"
}

if exists "$dest"; then had_dest=1; fi
if exists "$link"; then had_link=1; fi
swapping=1
if [ "$had_dest" -eq 1 ]; then
    backup_dir=$dest_parent/.$NAME.old.$$
    mv "$dest" "$backup_dir" || swap_failed "cannot move the old copy aside"
fi
if [ "$had_link" -eq 1 ]; then
    backup_link=$link_parent/.$NAME.oldlink.$$
    mv "$link" "$backup_link" || swap_failed "cannot move the old link aside"
fi
mv "$new_dir" "$dest" || swap_failed "cannot put the new copy in place"
new_dir=
mv "$new_link" "$link" || swap_failed "cannot put the new link in place"
new_link=
swapping=0

if [ -n "$backup_dir" ]; then rm -rf "$backup_dir"; backup_dir=; fi
if [ -n "$backup_link" ]; then rm -rf "$backup_link"; backup_link=; fi

# ---- warn when the skill is also in the other place --------------------------

other=
if [ "$mode" = user ]; then
    other=$repo_top
else
    other=${HOME:-}
fi
real_path() {
    (cd "$1" 2>/dev/null && pwd -P)
}
if [ -n "$other" ] && [ -d "$other" ] && [ "$(real_path "$other")" != "$(real_path "$root")" ]; then
    for p in "$other/.claude/skills/$NAME" "$other/.agents/skills/$NAME"; do
        if exists "$p"; then
            printf 'warning: %s also exists. With two copies, the agent may load either one or list the skill twice. Keep one of them.\n' "$p" >&2
        fi
    done
fi

printf 'installed %s %s into %s\n' "$NAME" "$label" "$dest"
printf 'linked %s -> %s\n' "$link" "$LINK_TARGET"
exit 0
