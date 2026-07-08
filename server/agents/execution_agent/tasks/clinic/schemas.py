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
                "Screen the current caller intake for a medical emergency. Reads the "
                "session file, assesses symptoms, and returns an urgency verdict. "
                "Never diagnoses or prescribes; errs toward escalation."
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


TriageLevel = Literal["emergency", "urgent", "routine"]


class TriageResult(BaseModel):
    """Structured triage verdict written back to session['triage']."""

    model_config = ConfigDict(extra="ignore")

    level: TriageLevel
    urgency: int = Field(ge=1, le=5, description="1 = lowest, 5 = call-911 emergency.")
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    red_flags: List[str] = Field(default_factory=list)


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
    "TriageResult",
    "TriageToolResult",
    "get_schemas",
]
