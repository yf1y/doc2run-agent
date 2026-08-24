from doc2run_agent.code_edits import apply_code_patch
from doc2run_agent.schemas import CodePatch


def test_exact_code_edit_changes_only_one_location():
    patch = CodePatch(edits=[{"old": "value = 1", "new": "value = 2"}])

    code, error = apply_code_patch("value = 1\nprint(value)\n", patch, allow_rewrite=False)

    assert error == ""
    assert code == "value = 2\nprint(value)\n"


def test_ambiguous_code_edit_is_rejected_without_changing_code():
    original = "print('x')\nprint('x')\n"
    patch = CodePatch(edits=[{"old": "print('x')", "new": "print('y')"}])

    code, error = apply_code_patch(original, patch, allow_rewrite=False)

    assert code == original
    assert "found 2" in error


def test_full_rewrite_requires_explicit_fallback():
    patch = CodePatch(replacement_code="print('fixed')")

    unchanged, error = apply_code_patch("raise RuntimeError()\n", patch, allow_rewrite=False)
    rewritten, rewrite_error = apply_code_patch(
        "raise RuntimeError()\n", patch, allow_rewrite=True
    )

    assert "only after" in error
    assert unchanged == "raise RuntimeError()\n"
    assert rewrite_error == ""
    assert rewritten == "print('fixed')\n"
