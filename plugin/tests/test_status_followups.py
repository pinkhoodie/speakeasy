"""Status questions on a call: never glued to the last request, recognized by the router model,
answered from what the task's session is really doing; follow-up notes don't script the voice."""
import json
import sqlite3

from speakeasy import calls, router, threads
from speakeasy.prompt import builder as P
from speakeasy.router import OpenTask


def test_a_short_status_question_is_not_glued_to_the_request_before_it():
    context = ("User: One thing is I don't want it to always say the same acknowledgement, it should fit the task\n"
               "User: How's that coming along")
    assert calls.whole_request(context) == "How's that coming along"
    # Speech split across two lines still joins.
    assert calls.whole_request("User: Bedroom lamps at forty percent\nUser: purple") == \
        "Bedroom lamps at forty percent purple"


def test_the_router_model_can_call_a_request_a_status_question():
    tasks = [OpenTask("t1", "Soften the acknowledgement", "working")]
    decision = router.parse_decision(json.dumps({"status_task_id": "t1", "follow_up_task_id": None, "parts": []}),
                                     "You appear to be stuck", tasks, [])
    assert decision and decision.parts[0].kind == router.STATUS and decision.parts[0].task_id == "t1"
    # An unknown id is not trusted.
    assert router.parse_decision(json.dumps({"status_task_id": "nope", "parts": ["x"]}), "x", tasks, []) \
        .parts[0].kind == router.NEW
    assert "status_task_id" in router.route_messages("x", tasks, [])[0]["content"]


def test_recent_steps_reads_tools_and_commentary(tmp_path):
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, tool_calls TEXT)")
    rows = [("s", "assistant", "", json.dumps([{"function": {"name": "read_file"}}])),
            ("s", "assistant", "Found the cue; changing it now.", json.dumps([{"function": {"name": "patch"}}])),
            ("s", "assistant", "Voice: earlier spoken line", ""),
            ("other", "assistant", "", json.dumps([{"function": {"name": "terminal"}}]))]
    con.executemany("INSERT INTO messages (session_id, role, content, tool_calls) VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()
    steps = threads.recent_steps(db, "s")
    assert steps == [("tool", "read_file"), ("said", "Found the cue; changing it now."), ("tool", "patch")]
    assert [P.tool_step(n) for k, n in steps if k == "tool"] == ["reading files", "editing code"]


def test_status_answers_use_live_steps_and_no_stock_phrase():
    text = P.status_answer("Soften the acknowledgement", "running", [], 200, None,
                           ["reading files", "editing code", "running commands"])
    assert "editing code; running commands" in text and "stock phrase" in text
    assert "live" not in P.status_answer("x", "completed", [], 10, "Done.", ["editing code"]).split("It finished")[1]


def test_follow_up_and_ack_notes_carry_no_script():
    note = P.added_to_task_note("the fantasy check")
    assert "one answer" not in note and "passed it on" not in note
    assert "On it" not in P.ack_now_note("check my fantasy team")
    rules = (P.PROMPT_DIR / "rules.md").read_text()
    for stock in ("On it", "Ooh, fun", "got it, adding that", "Give it a read"):
        assert stock not in rules
