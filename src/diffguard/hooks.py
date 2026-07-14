"""Git hook templates and installation.

Owns the shell-script payloads for the ``install-hook`` command and the
filesystem work of writing them. The CLI layer only translates the result
into user-facing output and exit codes.
"""

from __future__ import annotations

import os
import stat

from diffguard.git import get_hooks_dir

PRE_PUSH_HOOK = """\
#!/bin/sh
# DiffGuard pre-push hook — runs diffguard review on pushed changes
# Installed by: diffguard install-hook

remote="$1"

# Git represents a missing object with an all-zero object ID whose width follows
# the repository object format (40 hex digits for SHA-1, 64 for SHA-256). Keep
# this format-agnostic so the hook also works with future Git object formats.
is_zero_oid() {
    case "$1" in
        ''|*[!0]*) return 1 ;;
        *) return 0 ;;
    esac
}

while read -r _local_ref local_sha _remote_ref remote_sha; do
    if is_zero_oid "$local_sha"; then
        # Branch deletion — there is no local snapshot to review.
        continue
    fi

    if is_zero_oid "$remote_sha"; then
        # New branch — compare its commits against the default branch merge base.
        base_ref=$(git symbolic-ref --quiet "refs/remotes/$remote/HEAD" 2>/dev/null || echo "")
        if [ -z "$base_ref" ]; then
            for candidate in "refs/remotes/$remote/main" "refs/remotes/$remote/master" refs/heads/main refs/heads/master; do
                if git rev-parse --verify "$candidate^{commit}" >/dev/null 2>&1; then
                    base_ref="$candidate"
                    break
                fi
            done
        fi
        if [ -z "$base_ref" ]; then
            echo "DiffGuard could not determine a default branch for new-branch review." >&2
            exit 2
        fi
        base=$(git rev-parse --verify "$base_ref^{commit}" 2>/dev/null || echo "")
        if [ -z "$base" ]; then
            echo "DiffGuard could not resolve default branch $base_ref." >&2
            exit 2
        fi
        merge_base=$(git merge-base "$base" "$local_sha" 2>/dev/null || echo "")
        if [ -z "$merge_base" ]; then
            echo "DiffGuard could not find a merge base for the new branch." >&2
            exit 2
        fi
        range="$merge_base..$local_sha"
    else
        range="$remote_sha..$local_sha"
    fi

    echo "Running diffguard review $range ..."
    diffguard review "$range"
    status=$?
    if [ $status -eq 1 ]; then
        echo ""
        echo "DiffGuard found changes that need review (see above)."
        echo "Push anyway with: git push --no-verify"
        exit 1
    elif [ $status -ne 0 ]; then
        echo ""
        echo "DiffGuard failed with exit $status; blocking push."
        exit $status
    fi
done

exit 0
"""

PRE_COMMIT_HOOK = """\
#!/bin/sh
# DiffGuard pre-commit hook — runs diffguard review on staged changes
# Installed by: diffguard install-hook

echo "Running diffguard review --staged ..."
diffguard review --staged
status=$?
if [ $status -eq 1 ]; then
    echo ""
    echo "DiffGuard found changes that need review (see above)."
    echo "Commit anyway with: git commit --no-verify"
    exit 1
elif [ $status -ne 0 ]; then
    echo ""
    echo "DiffGuard failed with exit $status; blocking commit."
    exit $status
fi

exit 0
"""

HOOK_TEMPLATES: dict[str, str] = {
    "pre-push": PRE_PUSH_HOOK,
    "pre-commit": PRE_COMMIT_HOOK,
}


class HookError(Exception):
    """Raised when a git hook cannot be installed."""


def install_hook(repo: str, hook_type: str, *, force: bool = False) -> str:
    """Write *hook_type* into Git's configured hooks directory and return its path.

    Raises :class:`HookError` if *repo* is not a git repository or a hook
    already exists and *force* is False.
    """
    try:
        hooks_dir = str(get_hooks_dir(repo))
    except (OSError, RuntimeError) as exc:
        raise HookError(str(exc)) from exc
    os.makedirs(hooks_dir, exist_ok=True)

    hook_path = os.path.join(hooks_dir, hook_type)
    if os.path.exists(hook_path) and not force:
        raise HookError(f"Hook already exists: {hook_path}\nUse --force to overwrite.")

    with open(hook_path, "w") as f:
        f.write(HOOK_TEMPLATES[hook_type])

    # Make executable (owner/group/other)
    st = os.stat(hook_path)
    os.chmod(hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return hook_path
