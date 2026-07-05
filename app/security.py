from __future__ import annotations

import re
from typing import TypedDict

# ── PII patterns ────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE_RE = re.compile(r"\b(?:\+?\d{1,3}[-.\s]?)?(?:\(?\d{2,4}\)?[-.\s]?){2,4}\d{3,4}\b")
_CREDIT_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_SSN_LIKE_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")  # US SSN format specifically
_AADHAAR_LIKE_RE = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")  # 12-digit grouped IDs (e.g. Aadhaar)

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", _EMAIL_RE),
    ("ssn_like", _SSN_LIKE_RE),
    ("national_id_like", _AADHAAR_LIKE_RE),
    ("credit_card_like", _CREDIT_CARD_RE),
    ("phone", _PHONE_RE),
]

# ── Prompt-injection patterns ───────────────────────────────────────────
# Conservative, high-signal phrases. False positives here just mean a note
# gets flagged for a closer look (still saved, never silently dropped) —
# that's a deliberately safe failure direction.
_INJECTION_PATTERNS: list[str] = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) instructions?",
    r"disregard (all |any |the )?(previous|prior|above|earlier) instructions?",
    r"you are now",
    r"new instructions?:",
    r"system prompt",
    r"\bact as\b.{0,30}\b(admin|root|developer|system)\b",
    r"reveal (your|the) (system )?prompt",
    r"</?(system|instructions?|prompt)>",
    r"do anything now",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


class SanitizeResult(TypedDict):
    clean_text: str
    flagged: bool
    flagged_reasons: list[str]
    pii_types_redacted: list[str]


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Replace obviously-sensitive patterns with a labeled placeholder.

    Returns (redacted_text, list_of_pii_types_found). Order matters: email
    and ID-like patterns are checked before the broad phone/credit-card
    patterns so a 12-digit ID isn't double-matched as a phone number first.
    """
    found: list[str] = []
    redacted = text
    for label, pattern in _PII_PATTERNS:
        if pattern.search(redacted):
            found.append(label)
            redacted = pattern.sub(f"[REDACTED:{label.upper()}]", redacted)
    return redacted, found


def detect_injection(text: str) -> bool:
    """True if the text contains a high-signal prompt-injection pattern."""
    return bool(_INJECTION_RE.search(text))


def sanitize_user_notes(raw_text: str) -> SanitizeResult:
    """The single entry point the agent graph calls. Always returns a result
    — never raises — because a malformed note is exactly the kind of input
    this function exists to handle safely, not a reason to crash the graph.

    Behavior on a flagged injection attempt: the suspicious text is REMOVED
    from what reaches the journal_writer_agent's prompt (replaced with a
    neutral placeholder), not merely flagged-and-passed-through. PII is
    redacted regardless of injection status, since the two risks are
    independent of each other.
    """
    if not raw_text or not raw_text.strip():
        return {"clean_text": "", "flagged": False, "flagged_reasons": [], "pii_types_redacted": []}

    reasons: list[str] = []

    injection_found = detect_injection(raw_text)
    working_text = raw_text
    if injection_found:
        reasons.append("possible_prompt_injection")
        working_text = "[user note removed: contained instruction-like text]"

    redacted_text, pii_types = redact_pii(working_text)
    if pii_types:
        reasons.append("pii_redacted")

    # Hard length cap — independent of the above, this keeps one runaway
    # note from dominating the journal_writer_agent's prompt budget.
    MAX_NOTE_CHARS = 500
    if len(redacted_text) > MAX_NOTE_CHARS:
        redacted_text = redacted_text[:MAX_NOTE_CHARS] + "…"
        reasons.append("truncated")

    return {
        "clean_text": redacted_text,
        "flagged": bool(reasons),
        "flagged_reasons": reasons,
        "pii_types_redacted": pii_types,
    }
