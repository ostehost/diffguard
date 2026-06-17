"""Git hook templates and installation.

Owns the shell-script payloads for the ``install-hook`` command and the
filesystem work of writing them. The CLI layer only translates the result
into user-facing output and exit codes.
"""

from __future__ import annotations

import os
import stat

PRE_PUSH_HOOK = """\
#!/bin/sh
# DiffGuard pre-push hook — runs diffguard review on pushed changes
# Installed by: diffguard install-hook

remote="$1"
z40=0000000000000000000000000000000000000000

while read local_ref local_sha remote_ref remote_sha; do
    if [ "$remote_sha" = "$z40" ]; then
        # New branch — compare against main/master
        base=$(git rev-parse --verify refs/heads/main 2>/dev/null || git rev-parse --verify refs/heads/master 2>/dev/null || echo "")
        if [ -z "$base" ]; then
            continue
        fi
        range="$base..$local_sha"
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

echo "Running diffguard review --staged --no-deps ..."
diffguard review --staged --no-deps
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
    """Write the *hook_type* hook into *repo*'s ``.git/hooks`` and return its path.

    Raises :class:`HookError` if *repo* is not a git repository or a hook
    already exists and *force* is False.
    """
    git_dir = os.path.join(repo, ".git")
    if not os.path.isdir(git_dir):
        raise HookError(f"{repo} is not a git repository")

    hooks_dir = os.path.join(git_dir, "hooks")
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
