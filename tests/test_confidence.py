from vanta.core.confidence import ConfidenceDisposition, decide_confidence
from vanta.core.schemas import AutonomyMode


def test_confidence_bands_match_manual_starting_thresholds() -> None:
    assert (
        decide_confidence(0.49, AutonomyMode.ASSISTED, high_impact=False).disposition
        == ConfidenceDisposition.BLOCKED
    )
    assert (
        decide_confidence(0.69, AutonomyMode.ASSISTED, high_impact=False).disposition
        == ConfidenceDisposition.REQUIRES_APPROVAL
    )
    assert (
        decide_confidence(0.70, AutonomyMode.ASSISTED, high_impact=False).disposition
        == ConfidenceDisposition.ELIGIBLE
    )
    assert (
        decide_confidence(0.89, AutonomyMode.AUTO, high_impact=False).disposition
        == ConfidenceDisposition.REQUIRES_APPROVAL
    )
    assert (
        decide_confidence(0.90, AutonomyMode.AUTO, high_impact=False).disposition
        == ConfidenceDisposition.ELIGIBLE
    )


def test_manual_and_assisted_high_impact_steps_need_approval() -> None:
    assert (
        decide_confidence(1.0, AutonomyMode.MANUAL, high_impact=False).disposition
        == ConfidenceDisposition.REQUIRES_APPROVAL
    )
    assert (
        decide_confidence(1.0, AutonomyMode.ASSISTED, high_impact=True).disposition
        == ConfidenceDisposition.REQUIRES_APPROVAL
    )
