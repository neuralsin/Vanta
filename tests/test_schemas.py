from __future__ import annotations

import pytest
from pydantic import ValidationError

from vanta.core.schemas import (
    EditDecisionGraph,
    EditSpec,
    Evidence,
    Patch,
    Shot,
    SourceMedia,
    StyleCapsule,
)


def test_edit_spec_rejects_invalid_reference_weight() -> None:
    with pytest.raises(ValidationError):
        EditSpec(
            goal="reel",
            duration_s=10,
            aspect="9:16",
            fps=30,
            reference_weights={"reel-a": {"color": 1.1}},
        )


def test_edg_rejects_unknown_source_and_invalid_range() -> None:
    source = SourceMedia(id="clip-a", path="media/a.mp4", duration_frames=100)
    with pytest.raises(ValidationError):
        EditDecisionGraph(
            project_id="p",
            fps=30,
            timeline_name="ASSEMBLY_V1",
            sources=[source],
            shots=[
                Shot(
                    source_media_id="missing",
                    source_in_frame=0,
                    source_out_frame=20,
                    timeline_start_frame=0,
                )
            ],
        )
    with pytest.raises(ValidationError):
        Shot(
            source_media_id="clip-a",
            source_in_frame=20,
            source_out_frame=20,
            timeline_start_frame=0,
        )


def test_edg_refuses_implicit_mixed_rate_rounding_and_track_overlap() -> None:
    source = SourceMedia(id="clip-a", path="media/a.mp4", duration_frames=100, fps=23.976)
    with pytest.raises(ValidationError, match="mixed frame rates"):
        EditDecisionGraph(
            project_id="p",
            fps=30,
            timeline_name="VANTA_p_ASSEMBLY_V1",
            sources=[source],
            shots=[
                Shot(
                    source_media_id="clip-a",
                    source_in_frame=0,
                    source_out_frame=10,
                    timeline_start_frame=0,
                )
            ],
        )
    source.fps = 30
    with pytest.raises(ValidationError, match="overlaps"):
        EditDecisionGraph(
            project_id="p",
            fps=30,
            timeline_name="VANTA_p_ASSEMBLY_V1",
            sources=[source],
            shots=[
                Shot(
                    id="a",
                    source_media_id="clip-a",
                    source_in_frame=0,
                    source_out_frame=10,
                    timeline_start_frame=0,
                ),
                Shot(
                    id="b",
                    source_media_id="clip-a",
                    source_in_frame=10,
                    source_out_frame=20,
                    timeline_start_frame=9,
                ),
            ],
        )


def test_style_capsule_requires_evidence_and_observed_inferred_split() -> None:
    capsule = StyleCapsule(
        reference_id="ref-a",
        technique="cut_on_beat",
        window_s=(1, 2),
        observed="cut lands with beat",
        inferred={"method": "beat alignment"},
        evidence=[Evidence(source_id="ref-a", artifact="frame.jpg")],
        purpose="emphasis",
        confidence=0.8,
    )
    assert capsule.observed and capsule.inferred and capsule.evidence


def test_patch_contract_is_scoped_and_requires_reason() -> None:
    patch = Patch(
        target_edg_id="edg-a",
        dimension="grade",
        operation="adjust",
        target_id="shot-a",
        changes={"intensity": 0.4},
        reason="reduce saturation",
        confidence=0.8,
    )
    assert patch.requires_approval
