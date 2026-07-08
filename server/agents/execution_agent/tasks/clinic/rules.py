"""Rule-based / decision-tree triage for the clinic task.

Deterministic first-pass priority decision. No LLM call, no latency, and every
verdict is explainable: the result carries the matched rule ids and the exact
path taken through the tree.

DECISION TREE (evaluated top-down, first branch that fires wins):

    [1] intake empty?  ──yes──> routine (urgency 1, low confidence, need more info)
     │ no
    [2] emergency red flag matched? ──yes──> emergency (urgency 5) → 911, stop booking
     │ no
    [3] same-day rule matched? ──yes──> same_day (urgency 4)
     │ no
    [4] "soon" rule matched? ──yes──> soon (urgency 3)  [escalation modifier → same_day]
     │ no
    [5] routine rule matched? ──yes──> routine (urgency 2)  [escalation modifier → soon]
     │ no
    [6] UNMATCHED → caller (tool.py) falls back to the model screen; if that is
        unavailable, default conservatively to soon + needs_human_review.

Design rules:
- Conservative: ambiguity escalates, never de-escalates. Modifiers only bump
  priority UP one level and never create an "emergency" on their own —
  emergencies come only from explicit red-flag rules.
- Simple negation guard: "no chest pain" / "denies chest pain" does not fire
  the chest-pain rule.
- Sensitive situations (abuse, assault, grief, mental health) set
  ``needs_human_review`` so the interaction agent offers a human handoff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Rule definitions
# ---------------------------------------------------------------------------

LEVEL_EMERGENCY = "emergency"
LEVEL_SAME_DAY = "same_day"
LEVEL_SOON = "soon"
LEVEL_ROUTINE = "routine"

_URGENCY = {LEVEL_EMERGENCY: 5, LEVEL_SAME_DAY: 4, LEVEL_SOON: 3, LEVEL_ROUTINE: 2}
# One-step escalation ladder used by modifiers (never into emergency).
_ESCALATE = {LEVEL_ROUTINE: LEVEL_SOON, LEVEL_SOON: LEVEL_SAME_DAY, LEVEL_SAME_DAY: LEVEL_SAME_DAY}


@dataclass(frozen=True)
class Rule:
    """One explainable branch condition: any keyword match fires the rule."""

    id: str
    level: str
    keywords: Sequence[str]
    rationale: str


EMERGENCY_RULES: List[Rule] = [
    Rule("er-chest-pain", LEVEL_EMERGENCY,
         ("chest pain", "chest pressure", "chest tightness", "crushing pain", "heart attack"),
         "Possible cardiac event."),
    Rule("er-breathing", LEVEL_EMERGENCY,
         ("can't breathe", "cannot breathe", "difficulty breathing", "trouble breathing",
          "shortness of breath", "struggling to breathe", "gasping", "turning blue", "choking"),
         "Breathing difficulty."),
    Rule("er-stroke", LEVEL_EMERGENCY,
         ("face droop", "slurred speech", "arm weakness", "one side numb", "numb on one side",
          "weak on one side", "sudden confusion", "sudden vision loss", "stroke"),
         "Possible stroke signs."),
    Rule("er-bleeding", LEVEL_EMERGENCY,
         ("won't stop bleeding", "wont stop bleeding", "bleeding heavily", "uncontrolled bleeding",
          "severe bleeding", "vomiting blood", "coughing up blood"),
         "Severe or uncontrolled bleeding."),
    Rule("er-consciousness", LEVEL_EMERGENCY,
         ("passed out", "unconscious", "fainted", "unresponsive", "seizure", "convulsion"),
         "Loss of consciousness or seizure."),
    Rule("er-headache", LEVEL_EMERGENCY,
         ("worst headache", "sudden severe headache", "thunderclap headache"),
         "Sudden severe headache."),
    Rule("er-self-harm", LEVEL_EMERGENCY,
         ("suicid", "want to die", "end my life", "kill myself", "hurt myself",
          "self-harm", "self harm", "overdose"),
         "Possible self-harm risk."),
    Rule("er-anaphylaxis", LEVEL_EMERGENCY,
         ("throat swelling", "throat closing", "tongue swelling", "anaphyla", "severe allergic"),
         "Possible anaphylaxis."),
    Rule("er-trauma", LEVEL_EMERGENCY,
         ("car accident", "car crash", "gunshot", "stabbed", "hit by", "fell from"),
         "Major trauma."),
    Rule("er-poisoning", LEVEL_EMERGENCY,
         ("poisoning", "swallowed", "ingested chemical"),
         "Possible poisoning."),
    Rule("er-severe-abdomen", LEVEL_EMERGENCY,
         ("severe abdominal pain", "severe stomach pain", "rigid abdomen"),
         "Sudden severe abdominal pain."),
]

SAME_DAY_RULES: List[Rule] = [
    Rule("sd-high-fever", LEVEL_SAME_DAY,
         ("high fever", "fever of 103", "fever over 103", "104", "fever with rash", "stiff neck"),
         "High or complicated fever."),
    Rule("sd-infant-fever", LEVEL_SAME_DAY,
         ("baby fever", "infant fever", "newborn fever", "fever in my baby"),
         "Fever in an infant."),
    Rule("sd-vomiting", LEVEL_SAME_DAY,
         ("persistent vomiting", "can't keep anything down", "cannot keep anything down",
          "vomiting all day", "dehydrated", "dehydration"),
         "Persistent vomiting / dehydration risk."),
    Rule("sd-injury", LEVEL_SAME_DAY,
         ("broken", "fracture", "deep cut", "needs stitches", "need stitches",
          "dog bite", "animal bite", "human bite", "burn"),
         "Injury likely needing same-day care."),
    Rule("sd-severe-pain", LEVEL_SAME_DAY,
         ("severe pain", "unbearable pain", "pain is 9", "pain is 10", "worst pain"),
         "Severe pain."),
    Rule("sd-urinary-fever", LEVEL_SAME_DAY,
         ("burning urination with fever", "uti with fever", "kidney pain", "flank pain"),
         "Possible kidney involvement."),
    Rule("sd-eye", LEVEL_SAME_DAY,
         ("eye injury", "something in my eye", "sudden blurry vision"),
         "Eye injury / acute vision change."),
    Rule("sd-asthma", LEVEL_SAME_DAY,
         ("asthma worse", "wheezing", "inhaler not working"),
         "Worsening respiratory symptoms."),
]

SOON_RULES: List[Rule] = [
    Rule("so-fever", LEVEL_SOON, ("fever",), "Fever without red flags."),
    Rule("so-cough", LEVEL_SOON,
         ("persistent cough", "cough for", "bad cough", "bronchitis"),
         "Persistent cough."),
    Rule("so-ent", LEVEL_SOON,
         ("sore throat", "ear pain", "earache", "ear infection", "sinus"),
         "ENT symptoms."),
    Rule("so-urinary", LEVEL_SOON,
         ("burning urination", "painful urination", "uti", "urinary"),
         "Urinary symptoms."),
    Rule("so-rash-spreading", LEVEL_SOON,
         ("spreading rash", "rash spreading", "infected", "pus", "red streaks"),
         "Possibly infected / spreading skin issue."),
    Rule("so-migraine", LEVEL_SOON, ("migraine", "bad headache"), "Significant headache."),
    Rule("so-pain", LEVEL_SOON,
         ("back pain", "abdominal pain", "stomach pain", "joint pain", "sprain",
          "swelling", "swollen"),
         "Moderate pain or swelling."),
    Rule("so-mental-health", LEVEL_SOON,
         ("anxiety", "panic attack", "depressed", "depression", "can't sleep", "insomnia"),
         "Mental-health concern (non-crisis)."),
    Rule("so-vomit-diarrhea", LEVEL_SOON,
         ("vomiting", "diarrhea", "nausea"),
         "GI symptoms."),
]

ROUTINE_RULES: List[Rule] = [
    Rule("ro-refill", LEVEL_ROUTINE,
         ("refill", "renew prescription", "prescription renewal", "medication renewal"),
         "Medication refill."),
    Rule("ro-checkup", LEVEL_ROUTINE,
         ("annual physical", "checkup", "check-up", "check up", "physical exam",
          "follow-up", "follow up", "routine"),
         "Routine visit / follow-up."),
    Rule("ro-vaccine", LEVEL_ROUTINE,
         ("vaccine", "vaccination", "flu shot", "immunization", "booster"),
         "Vaccination."),
    Rule("ro-results", LEVEL_ROUTINE,
         ("test results", "lab results", "blood work results"),
         "Results review."),
    Rule("ro-admin", LEVEL_ROUTINE,
         ("referral", "paperwork", "form", "insurance question", "medical records"),
         "Administrative request."),
    Rule("ro-minor", LEVEL_ROUTINE,
         ("mild cold", "runny nose", "stuffy nose", "minor rash", "seasonal allergies",
          "allergies acting up", "cold symptoms"),
         "Minor self-limited symptoms."),
]

# Modifiers bump a non-emergency verdict UP one level (routine→soon, soon→same_day).
ESCALATION_MODIFIERS: List[Rule] = [
    Rule("mod-worsening", "modifier",
         ("getting worse", "worsening", "much worse", "not improving", "keeps coming back"),
         "Symptoms reported as worsening."),
    Rule("mod-duration", "modifier",
         ("for a week", "over a week", "for weeks", "several days", "won't go away", "wont go away"),
         "Prolonged duration."),
    Rule("mod-high-risk", "modifier",
         ("pregnant", "pregnancy", "infant", "newborn", "elderly", "diabet",
          "heart condition", "immunocompromised", "chemo", "transplant"),
         "High-risk caller."),
]

# Sensitive situations → recommend a human handoff regardless of priority.
SENSITIVE_KEYWORDS: Sequence[str] = (
    "abuse", "abusive", "domestic violence", "assault", "assaulted", "rape",
    "eating disorder", "miscarriage", "stillbirth", "passed away", "died",
)

_NEGATION = re.compile(
    r"\b(?:no|not|without|denies|deny|denied|never)\b(?:\W+\w+){0,2}\W+$"
)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class RuleDecision:
    """Outcome of one pass through the decision tree."""

    level: Optional[str]  # None => unmatched → caller should use model fallback
    urgency: int
    confidence: float
    rationale: str
    red_flags: List[str] = field(default_factory=list)
    matched_rules: List[str] = field(default_factory=list)
    decision_path: List[str] = field(default_factory=list)
    needs_human_review: bool = False

    @property
    def matched(self) -> bool:
        return self.level is not None


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------
def _normalize(symptoms: Iterable[str], notes: Optional[str]) -> str:
    parts = [s.strip().lower() for s in symptoms if s and s.strip()]
    if notes:
        parts.append(notes.strip().lower())
    return " . ".join(parts)


def _keyword_hit(text: str, keyword: str) -> bool:
    """True when *keyword* occurs and is not negated ("no chest pain").

    The negation lookback never crosses a sentence/symptom boundary ("." or ","),
    so "no chest pain . mild cold" still lets "mild cold" fire.
    """
    start = 0
    while (idx := text.find(keyword, start)) != -1:
        window = text[max(0, idx - 32):idx]
        # keep only the current clause (after the last separator)
        for sep in (".", ","):
            if sep in window:
                window = window.rsplit(sep, 1)[-1]
        if not _NEGATION.search(window):
            return True
        start = idx + len(keyword)
    return False


def _first_matches(text: str, rules: Sequence[Rule]) -> List[Rule]:
    return [r for r in rules if any(_keyword_hit(text, k) for k in r.keywords)]


# ---------------------------------------------------------------------------
# The decision tree
# ---------------------------------------------------------------------------
def evaluate_rules(symptoms: Iterable[str], notes: Optional[str] = None) -> RuleDecision:
    """Walk the triage decision tree over the caller's intake text."""
    text = _normalize(symptoms, notes)
    path: List[str] = []
    sensitive = [k for k in SENSITIVE_KEYWORDS if _keyword_hit(text, k)]

    # [1] Nothing to judge yet.
    if not text:
        path.append("[1] intake empty -> routine (low confidence, need more info)")
        return RuleDecision(
            level=LEVEL_ROUTINE, urgency=1, confidence=0.2,
            rationale="No symptoms recorded yet; more intake information is needed.",
            decision_path=path,
        )
    path.append("[1] intake present")

    # [2] Emergency red flags.
    if hits := _first_matches(text, EMERGENCY_RULES):
        path.append(f"[2] emergency red flag(s): {', '.join(r.id for r in hits)}")
        return RuleDecision(
            level=LEVEL_EMERGENCY, urgency=5, confidence=0.95,
            rationale="Red-flag symptoms matched: " + "; ".join(r.rationale for r in hits),
            red_flags=[r.rationale for r in hits],
            matched_rules=[r.id for r in hits],
            decision_path=path,
            needs_human_review=bool(sensitive),
        )
    path.append("[2] no emergency red flags")

    modifiers = _first_matches(text, ESCALATION_MODIFIERS)

    # [3]-[5] Priority tiers, highest first; first tier with a match decides.
    for node, tier, conf in (
        ("[3]", SAME_DAY_RULES, 0.85),
        ("[4]", SOON_RULES, 0.8),
        ("[5]", ROUTINE_RULES, 0.85),
    ):
        hits = _first_matches(text, tier)
        if not hits:
            path.append(f"{node} no match")
            continue
        level = hits[0].level
        path.append(f"{node} matched: {', '.join(r.id for r in hits)} -> {level}")
        rationale = "; ".join(r.rationale for r in hits)
        if modifiers and level != LEVEL_SAME_DAY:
            escalated = _ESCALATE[level]
            path.append(
                f"[mod] escalation modifier(s) {', '.join(m.id for m in modifiers)}: "
                f"{level} -> {escalated}"
            )
            rationale += " Escalated one level: " + "; ".join(m.rationale for m in modifiers)
            level = escalated
        if sensitive:
            path.append(f"[sens] sensitive keyword(s): {', '.join(sensitive)} -> human review")
        return RuleDecision(
            level=level, urgency=_URGENCY[level], confidence=conf,
            rationale=rationale,
            matched_rules=[r.id for r in hits] + [m.id for m in modifiers],
            decision_path=path,
            needs_human_review=bool(sensitive),
        )

    # [6] Symptoms exist but nothing matched → let the caller decide the fallback.
    path.append("[6] symptoms unmatched by any rule -> fallback")
    return RuleDecision(
        level=None, urgency=3, confidence=0.0,
        rationale="Reported symptoms did not match any triage rule.",
        decision_path=path,
        needs_human_review=True,
    )


def conservative_default(decision: RuleDecision) -> RuleDecision:
    """Safety net when the model fallback is unavailable: err upward to 'soon'."""
    decision.level = LEVEL_SOON
    decision.urgency = _URGENCY[LEVEL_SOON]
    decision.confidence = 0.3
    decision.rationale += (
        " Model fallback unavailable; conservatively assigned 'soon' and flagged "
        "for human review."
    )
    decision.decision_path.append("[6b] model unavailable -> conservative 'soon' + human review")
    decision.needs_human_review = True
    return decision


__all__ = [
    "Rule",
    "RuleDecision",
    "evaluate_rules",
    "conservative_default",
    "LEVEL_EMERGENCY",
    "LEVEL_SAME_DAY",
    "LEVEL_SOON",
    "LEVEL_ROUTINE",
]
