"""Git ref-range parsing shared across the CLI and pipeline layers."""

from __future__ import annotations


def split_ref_range(ref_range: str) -> tuple[str, str]:
    """Split a git ref range into ``(old_ref, new_ref)``.

    ``"A..B"`` → ``("A", "B")``. A bare ref with no ``..`` resolves to
    ``(f"{ref}~1", ref)`` — i.e. that ref compared against its parent.
    """
    match ref_range.split(".."):
        case [old, new]:
            return old, new
        case _:
            return f"{ref_range}~1", ref_range
