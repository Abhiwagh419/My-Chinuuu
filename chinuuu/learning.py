"""Conservative preference and repeated-behaviour detection.

This module proposes memories; it never approves or saves one automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class LearningSignal:
    key: str
    category: str
    proposed_memory: str
    evidence: str


_EXPLICIT_PATTERNS = (
    r"\bfrom now on\b", r"\bin future\b", r"\bi prefer\b", r"\bi'd prefer\b",
    r"\bplease always\b", r"\balways\b", r"\bnever\b", r"\bdon't do that\b",
    r"\bdo not do that\b", r"\bremember that\b", r"\bmy preference is\b",
    r"\bi want you to\b", r"\byou must\b", r"\bi expect you to\b",
)

# Three distinct matching messages within 30 days are required before a
# repeated-pattern suggestion is generated. These are suggestions, not facts.
_BEHAVIOR_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    ("step_by_step", "working_style", "Prefers step-by-step guidance for tasks.",
     r"\b(step[- ]by[- ]step|stepwise|walk me through|one step at a time)\b"),
    ("practical_examples", "learning_style", "Prefers practical or real-world examples in explanations.",
     r"\b(practical (engineering )?examples?|real[- ]world examples?|hands[- ]on examples?)\b"),
    ("concise_answers", "communication", "Prefers concise, direct answers without unnecessary filler.",
     r"\b(be concise|keep it short|no fluff|straight to the point|briefly)\b"),
    ("detailed_explanations", "learning_style", "Prefers detailed explanations when learning technical concepts.",
     r"\b(in detail|more detail|detailed explanation|deep dive|explain thoroughly)\b"),
    ("clarify_before_action", "working_style", "Prefers clarifying questions before execution when requirements are incomplete.",
     r"\b(ask (me )?(clarifying )?questions|clarify .*before|don't assume|do not assume)\b"),
    ("truthful_feedback", "communication", "Prefers candid, evidence-based feedback rather than automatic agreement.",
     r"\b(be honest|tell me if .*wrong|don't just agree|do not just agree|truth above comfort|dead true|brutally honest)\b"),
)


def explicit_preference_candidate(message: str) -> str | None:
    text = " ".join(message.strip().split())
    if not 12 <= len(text) <= 500:
        return None
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _EXPLICIT_PATTERNS):
        return text
    return None


def detect_behavior_signals(message: str) -> list[LearningSignal]:
    text = " ".join(message.strip().split())
    if len(text) < 12 or len(text) > 1000:
        return []
    signals: list[LearningSignal] = []
    for key, category, memory, pattern in _BEHAVIOR_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            signals.append(LearningSignal(key, category, memory, text[:300]))
    return signals


def preference_candidate(message: str) -> str | None:
    """Backward-compatible alias used by earlier versions/tests."""
    return explicit_preference_candidate(message)


def category_for_explicit_preference(message: str) -> str:
    text = message.lower()
    if any(word in text for word in ("explain", "learning", "learn", "example", "step-by-step", "technical concept")):
        return "learning_style"
    if any(word in text for word in ("honest", "truth", "agree", "tone", "concise", "detailed", "response")):
        return "communication"
    if any(word in text for word in ("ask", "clarify", "before", "workflow", "execute", "task")):
        return "working_style"
    return "preference"
