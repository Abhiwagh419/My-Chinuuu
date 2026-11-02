from chinuuu.planner import ToolExecutionPolicy, build_plan_prompt


def test_plan_prompt_requires_readiness_assessment():
    prompt = build_plan_prompt("Organise project files", "D:/safe/workspace", [])
    assert "NEEDS CLARIFICATION" in prompt
    assert "Do not execute tools" in prompt


def test_tool_policy_enforces_limits():
    policy = ToolExecutionPolicy(max_tool_rounds=3, max_tool_calls=5)
    assert policy.validate_call_count(2, 3)
    assert not policy.validate_call_count(4, 2)
