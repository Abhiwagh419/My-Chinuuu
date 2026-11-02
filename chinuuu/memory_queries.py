"""Deterministic answers for memory-status questions.

Memory existence and contents are facts held by SQLite, so the LLM should not
be the authority for answering whether a record exists.
"""
from __future__ import annotations

import re

_STOP_WORDS = {
    "what", "do", "you", "i", "my", "me", "about", "the", "a", "an",
    "is", "are", "of", "to", "and", "or", "your", "have", "remember",
    "know", "stored", "persistent", "memory", "memories", "preference",
    "preferences", "please", "tell", "show", "list", "currently",
}
_MEMORY_QUESTION = re.compile(
    r"\b(what do you (remember|know)|what have you remembered|"
    r"show (me )?(my )?(saved )?memories|list (my )?(saved )?memories|"
    r"stored memory|persistent memory|my preferences)\b",
    re.IGNORECASE,
)


def answer_memory_query(question: str, memories: list[dict[str, object]]) -> str | None:
    """Return a factual local-DB answer if the question is about memory."""
    if not _MEMORY_QUESTION.search(question):
        return None

    if not memories:
        return (
            "Abhi, I checked my local memory database. There are currently no "
            "approved long-term memories saved. Information in this conversation "
            "may still be available in chat history, but it is not a confirmed "
            "persistent preference."
        )

    tokens = {
        token.lower()
        for token in re.findall(r"[a-zA-Z]{3,}", question)
        if token.lower() not in _STOP_WORDS
    }

    if tokens:
        relevant = []
        for item in memories:
            content = str(item.get("content", ""))
            content_tokens = {t.lower() for t in re.findall(r"[a-zA-Z]{3,}", content)}
            if tokens & content_tokens:
                relevant.append(item)
    else:
        relevant = list(memories)

    if not relevant:
        relevant = list(memories)
        intro = (
            "Abhi, I do have approved persistent memories, but none clearly match "
            "the specific topic in your question. Here is the complete saved list:"
        )
    else:
        intro = "Abhi, I checked the local SQLite memory database. These approved persistent memories are saved:"

    lines = [intro]
    for item in relevant:
        memory_id = item.get("id", "?")
        category = item.get("category", "preference")
        content = str(item.get("content", ""))
        lines.append(f"\n- Memory #{memory_id} [{category}]: {content}")
    lines.append("\nThese records persist across Chinuuu restarts. I will use relevant ones in future sessions.")
    return "".join(lines)
