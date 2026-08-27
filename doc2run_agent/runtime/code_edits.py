"""Apply exact, reviewable edits to generated Python source code."""

from __future__ import annotations

from ..schemas import CodePatch
from .runner import sanitize_code


def apply_code_patch(current_code: str, patch: CodePatch, *, allow_rewrite: bool) -> tuple[str, str]:
    """Apply exact replacements and return ``(code, error)``.

    Exact replacement is deliberately stricter than a fuzzy patch: a weak model
    cannot silently edit the wrong occurrence.  Full replacement is available
    only after the caller has explicitly enabled the fallback.
    """

    if patch.replacement_code.strip():
        if not allow_rewrite:
            return current_code, "A full rewrite is allowed only after earlier local edits failed"
        replacement = sanitize_code(patch.replacement_code)
        if not replacement:
            return current_code, "Replacement code is empty"
        return replacement, ""

    if not patch.edits:
        return current_code, "The patch did not contain any edits"

    updated = current_code
    for index, edit in enumerate(patch.edits, start=1):
        if not edit.old:
            return current_code, f"Edit {index} has an empty old value"
        if edit.old == edit.new:
            return current_code, f"Edit {index} does not change the code"
        occurrences = updated.count(edit.old)
        if occurrences != 1:
            return current_code, (
                f"Edit {index} expected one exact match but found {occurrences}; "
                "the code was left unchanged"
            )
        updated = updated.replace(edit.old, edit.new, 1)

    return sanitize_code(updated), ""
