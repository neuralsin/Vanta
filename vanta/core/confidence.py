"""Manual-defined confidence policy for executable plan steps."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from vanta.core.schemas import AutonomyMode


class ConfidenceDisposition(StrEnum):
    BLOCKED = "BLOCKED"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"
    ELIGIBLE = "ELIGIBLE"


@dataclass(frozen=True)
class ConfidenceDecision:
    disposition: ConfidenceDisposition
    reason: str


def decide_confidence(
    confidence: float,
    autonomy: AutonomyMode,
    *,
    high_impact: bool,
) -> ConfidenceDecision:
    """Apply the manual's starting 0.50/0.70/0.90 gate bands.

    These thresholds are intentionally recorded as policy values rather than
    scattered in executor branches so later calibration can replace them.
    """
    if confidence < 0.50:
        return ConfidenceDecision(ConfidenceDisposition.BLOCKED, "confidence is below 0.50")
    if autonomy == AutonomyMode.MANUAL:
        return ConfidenceDecision(
            ConfidenceDisposition.REQUIRES_APPROVAL, "MANUAL mode requires approval"
        )
    if confidence < 0.70:
        return ConfidenceDecision(
            ConfidenceDisposition.REQUIRES_APPROVAL,
            "confidence is between 0.50 and 0.70",
        )
    if autonomy == AutonomyMode.ASSISTED and high_impact:
        return ConfidenceDecision(
            ConfidenceDisposition.REQUIRES_APPROVAL,
            "ASSISTED mode requires approval for high-impact work",
        )
    if autonomy == AutonomyMode.AUTO and confidence < 0.90:
        return ConfidenceDecision(
            ConfidenceDisposition.REQUIRES_APPROVAL,
            "AUTO mode may execute without approval only at confidence 0.90 or above",
        )
    return ConfidenceDecision(
        ConfidenceDisposition.ELIGIBLE, "confidence and autonomy policy allow execution"
    )
