from chinuuu.memory_queries import answer_memory_query


def test_saved_memory_is_reported_for_related_question():
    memories = [{
        "id": 1,
        "category": "preference",
        "content": "From now on, always explain technical concepts using practical engineering examples.",
    }]
    result = answer_memory_query("What do you remember about my technical explanation preferences?", memories)
    assert result is not None
    assert "Memory #1" in result
    assert "technical concepts" in result
    assert "no approved" not in result.lower()


def test_empty_memory_is_reported_truthfully():
    result = answer_memory_query("What do you remember about me?", [])
    assert result is not None
    assert "no approved long-term memories" in result.lower()


def test_non_memory_question_is_not_intercepted():
    assert answer_memory_query("Explain a Python decorator", []) is None
