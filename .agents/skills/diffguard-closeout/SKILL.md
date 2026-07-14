---
name: diffguard-closeout
description: Run DiffGuard once near task completion against the current staged, unstaged, added, and deleted worktree state; resolve or explain structured contract findings and analysis gaps before running repository checks. Use for final review, finish, closeout, pre-handoff, or pre-commit verification in this repository.
---

# DiffGuard Closeout

Use this bounded finish workflow after implementation and focused tests are complete. Do not run a full scan after every edit.

1. Resolve the comparison base. Prefer `origin/main`; fetch only when the task authorizes network access. If it is unavailable, use the repository's known target branch or ask rather than guessing a materially different base.
2. Run once through the bundled POSIX resource-bound wrapper and preserve
   the exit status:

   ```bash
   set +e
   python3 .agents/skills/diffguard-closeout/scripts/run_review.py \
     --base origin/main \
     --timeout-seconds 300 \
     --max-output-bytes 10485760
   rc=$?
   set -e
   ```

   The wrapper supports the macOS/Linux POSIX environments used by the shell
   hooks; it is not a Windows runner. It uses Python's standard library rather
   than platform-specific `timeout` commands and invokes the project through
   `uv run --locked`. It enforces a five-minute wall limit and a 10 MiB combined
   stdout/stderr limit, terminates the review process group when a bound is
   reached, and maps timeout, output overflow, launch failure, signal
   termination, or an unexpected child status to exit `2`. Keep finite bounds
   on every run; increase one only with an evidence-based reason and record the
   override in the handback.

   The wrapper creates unique review and stderr artifacts with mode `0600` and
   prints both paths as JSON strings so path control characters cannot alter a
   terminal/log. It retains both artifacts by default. Never replace them with
   shared fixed paths or redirect the review around the wrapper.
3. Record both artifact paths immediately. Validate/read the review JSON and
   inspect the bounded stderr artifact. Interpret `rc` exactly:

   - `0`: no findings. Still inspect `warnings`, `stats.parse_errors`, and analysis gaps.
   - `1`: findings exist. Resolve each finding or record a specific, evidence-based explanation for leaving it.
   - `2`: tool or resource-bound error. Inspect the retained artifacts, fix the invocation/repository/resource problem, and never treat partial or invalid JSON as a clean result.

4. Treat `references` as unresolved syntactic name evidence. Use their `kind`, `confidence`, `resolution`, and `evidence`; never upgrade them to exact callers without independent ownership proof.
5. If resolving a finding changes code, rerun focused tests and repeat the worktree review. Stop when the final JSON and every warning/gap are explained; avoid review loops with no intervening change.
6. Run the repository closeout gates:

   ```bash
   uv lock --check
   just ci
   just validate-corpus
   just docs-build
   just build
   ```

7. Hand back the review command/status, applied bounds, both artifact paths,
   findings resolved or explicitly accepted, warnings/gaps, and check outcomes.
   State who owns artifact cleanup. Do not commit, push, publish, or post
   comments unless separately authorized.
8. Preserve the private artifacts through final review/handoff by default; they
   are the forensic receipt for this exact dirty tree. Delete them only after
   the receiving operator or agent acknowledges the handoff, or when an
   explicit retention policy requires cleanup. Record the cleanup before using
   `rm -f --` on the exact unique paths. Never use a glob or clean the shared
   temporary directory.
