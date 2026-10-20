"""The live status rule: narrate real steps (including background work), never filler or raw reasoning."""
from speakeasy.prompt import builder as P


def test_status_rule_narrates_steps_and_background_work_without_filler():
    rule = P.status_rule(P.Names("Ava", "Sam"))
    # The two-line format the app parses is unchanged.
    assert "STATUS: <" in rule and "DETAIL: <" in rule
    assert "DONE: <" in rule and "SPOKEN: <" in rule
    # A fresh pair at every meaningful step, including delegated/background work and just before the answer.
    assert "every meaningful step" in rule
    assert "subagent" in rule and "background" in rule
    assert "just before you write the final answer" in rule
    # No filler, no repeats, and still no raw reasoning, credentials or tool arguments.
    assert "Skip a pair that would only repeat the last one" in rule and "still working" in rule
    assert "no step-by-step reasoning, credentials, or raw tool arguments" in rule
    # Placeholders were rendered, never left raw.
    assert "{user_name" not in rule and "Sam" in rule


def test_every_task_prompt_carries_the_status_rule():
    names = P.Names("Ava", "Sam")
    rule = P.status_rule(names)
    assert rule in P.build_task_prompt(names, 1, "User: check the weather", "check the weather")
