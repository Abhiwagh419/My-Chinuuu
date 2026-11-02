from chinuuu.learning import (
    category_for_explicit_preference,
    detect_behavior_signals,
    explicit_preference_candidate,
)


def test_detects_explicit_preference():
    assert explicit_preference_candidate("From now on, keep answers concise.") is not None


def test_ignores_ordinary_message():
    assert explicit_preference_candidate("What is a Python decorator?") is None


def test_detects_repeated_style_signal():
    signals = detect_behavior_signals("Please explain this step by step with a practical example.")
    keys = {signal.key for signal in signals}
    assert "step_by_step" in keys
    assert "practical_examples" in keys


def test_explicit_preference_category():
    assert category_for_explicit_preference("Always explain technical concepts with examples.") == "learning_style"
