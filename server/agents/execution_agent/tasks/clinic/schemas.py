"""Schemas for the clinic triage task (mirror of tasks/search_email/schemas.py)."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

TASK_TOOL_NAME = "triage_screen"

_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": TASK_TOOL_NAME,
            "description": (
                "Screen the current caller intake for a medical emergency and assign "
                "a priority (emergency / same_day / soon / routine). Decision is made "
                "by an explainable rule-based decision tree; a model screen is used "
                "only as a fallback for unmatched symptoms. Never diagnoses or "
                "prescribes; errs toward escalation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "call_id": {
                        "type": "string",
                        "description": "Call id whose session file should be screened.",
                    },
                },
                "required": ["call_id"],
                "additionalProperties": False,
            },
        },
    }
]


# Priority levels:
#   emergency -> tell caller to hang up and dial 911; no booking
#   same_day  -> book today
#   soon      -> book within 2-3 days
#   routine   -> next available routine slot / follow-up
TriageLevel = Literal["emergency", "same_day", "soon", "routine"]

# How the verdict was produced (for explainability / auditing):
#   rules                -> deterministic decision tree (rules.py)
#   model_fallback       -> LLM screen, used only when no rule matched
#   default_conservative -> rules unmatched AND model unavailable; safe default
TriageSource = Literal["rules", "model_fallback", "default_conservative"]


class TriageResult(BaseModel):
    """Structured triage verdict written back to session['triage']."""

    model_config = ConfigDict(extra="ignore")

    level: TriageLevel
    urgency: int = Field(ge=1, le=5, description="1 = lowest, 5 = call-911 emergency.")
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    red_flags: List[str] = Field(default_factory=list)
    # --- explainability trail (rule-based path) ---
    source: TriageSource = "rules"
    matched_rules: List[str] = Field(
        default_factory=list, description="Ids of decision-tree rules that fired."
    )
    decision_path: List[str] = Field(
        default_factory=list, description="Human-readable trace through the tree."
    )
    needs_human_review: bool = Field(
        default=False,
        description=(
            "True when the verdict is uncertain or the situation is sensitive; the "
            "interaction agent should offer a human handoff."
        ),
    )


class TriageToolResult(BaseModel):
    """Envelope returned to the execution-agent tool loop."""

    status: Literal["success", "error"]
    call_id: Optional[str] = None
    triage: Optional[TriageResult] = None
    error: Optional[str] = None


def get_schemas() -> List[Dict[str, Any]]:
    """Return the JSON schema for the triage task."""
    return _SCHEMAS


__all__ = [
    "TASK_TOOL_NAME",
    "TriageLevel",
    "TriageSource",
    "TriageResult",
    "TriageToolResult",
    "get_schemas",
]
