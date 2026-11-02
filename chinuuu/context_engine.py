from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Stop words keep common conversational glue from dominating retrieval.
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "could",
    "did", "do", "does", "for", "from", "get", "give", "have", "how", "i",
    "in", "is", "it", "me", "my", "of", "on", "or", "please", "show", "tell",
    "that", "the", "their", "them", "there", "this", "to", "was", "what", "when",
    "where", "which", "who", "why", "will", "with", "you", "your",
}


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9_+#.-]{2,}", value.casefold()) if token not in _STOP_WORDS]


def rank_memories(query: str, memories: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Rank approved memories deterministically; never infer or create memories here."""
    if not memories or limit <= 0:
        return []
    query_tokens = Counter(_tokens(query))
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for position, memory in enumerate(memories):
        content = str(memory.get("content", ""))
        memory_tokens = Counter(_tokens(content))
        overlap = sum(min(count, memory_tokens[token]) for token, count in query_tokens.items())
        phrase_bonus = 2.0 if query.strip() and query.casefold() in content.casefold() else 0.0
        category_bonus = 0.15 if overlap and str(memory.get("category", "")).casefold() in {"preference", "project"} else 0.0
        score = float(overlap) + phrase_bonus + category_bonus
        scored.append((score, position, memory))
    relevant = [item for item in scored if item[0] > 0]
    if relevant:
        relevant.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [item[2] for item in relevant[:limit]]
    # On unrelated turns, retain a small stable tail instead of flooding context.
    return [item for item in memories[-min(limit, 4):]]


def format_memory_context(memories: list[dict[str, Any]]) -> list[str]:
    return [
        f"Memory #{item.get('id', '?')} [{item.get('category', 'general')}]: {item.get('content', '')}"
        for item in memories
    ]
