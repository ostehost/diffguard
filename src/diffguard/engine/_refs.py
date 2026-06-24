"""Git ref-range parsing shared across the CLI and pipeline layers."""

from __future__ import annotations


def split_ref_range(ref_range: str) -> tuple[str, str]:
    """Split a git ref range into ``(old_ref, new_ref)`` for content lookup.

    Pure string handling only — no git access. The git-semantic cases that need
    a repository (resolving the merge-base a three-dot range implies) are
    normalized away by the git-aware layer *before* a range reaches here; see
    ``cli._normalize_ref_range``. This function therefore handles:

    - ``"A..B"`` → ``("A", "B")``
    - ``"A...B"`` → ``("A", "B")`` — a best-effort fallback for callers that
      pass an un-normalized three-dot range straight to the pipeline. The
      endpoints approximate the symmetric difference; the merge-base proper is
      resolved upstream when a repo is available.
    - an omitted endpoint mirrors git: ``"..B"`` → ``("HEAD", "B")``,
      ``"A.."`` → ``("A", "HEAD")`` (git reads ``A..`` as ``A..HEAD``)
    - a bare ref → ``(f"{ref}~1", ref)`` — that ref against its parent

    Split on the first separator only (``partition``) so a leading dot can't
    leak into ``new_ref``.
    """
    if "..." in ref_range:
        old, _, new = ref_range.partition("...")
    elif ".." in ref_range:
        old, _, new = ref_range.partition("..")
    else:
        return f"{ref_range}~1", ref_range
    return old or "HEAD", new or "HEAD"
