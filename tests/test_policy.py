from vanta.core.policy import validate_patch
from vanta.core.schemas import Patch


def test_low_confidence_patch_cannot_bypass_approval() -> None:
    patch = Patch(
        target_edg_id="edg-a",
        dimension="text",
        operation="move",
        target_id="shot-a",
        changes={"x": 0.5},
        reason="avoid face",
        confidence=0.6,
        requires_approval=False,
    )
    result = validate_patch(patch)
    assert not result.allowed


def test_high_confidence_scoped_patch_is_structurally_allowed() -> None:
    patch = Patch(
        target_edg_id="edg-a",
        dimension="grade",
        operation="adjust",
        target_id="shot-a",
        changes={"saturation": -0.1},
        reason="lower intensity",
        confidence=0.9,
    )
    assert validate_patch(patch).allowed
