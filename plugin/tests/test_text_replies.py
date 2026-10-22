"""Text replies: the app's expanded chat view shows a finished task's full written answer, while the
realtime voice still speaks the short spoken line exactly as before (text is additive, not a mode)."""
from __future__ import annotations

from fakes import SDP, http, wait_for

ANSWER = ("Three flights fit your window.\n\n"
          "1. **UA 123** leaves 8:05 and lands 10:40, $214.\n"
          "2. **F9 77** leaves 11:30 and lands 2:05, $129.\n"
          "3. **WN 410** leaves 6:15 pm, $188.\n"
          "SPOKEN: Three flights fit; the cheapest is Frontier at 11:30.")


def start_call(server, service):
    status, session = http(server.base_url, "POST", "/voice/sessions", {"sdp": SDP}, server.token,
                           {"Idempotency-Key": "req_text_replies"})
    assert status == 201, session
    return service.workers[-1]


def tasks(server):
    return http(server.base_url, "GET", "/voice/work/latest", token=server.token)[1]["tasks"]


def spoken(worker):
    return [c for k, _, c in worker.sent if k == "session.commentary.append"]


def test_full_answer_reaches_the_app_and_the_voice_still_speaks_the_short_line(server, service, hermes):
    hermes.responder = lambda prompt, sid: ANSWER
    worker = start_call(server, service)
    worker.delegate("call_tr", "Find me flights to Denver on Friday")
    done = wait_for(lambda: [t for t in tasks(server) if t["status"] == "completed"])[0]

    # The app reads tasks[].result.full for the text reply: the whole answer, without the SPOKEN line.
    full = done["result"]["full"]
    assert "UA 123" in full and "F9 77" in full and "WN 410" in full
    assert "SPOKEN:" not in full
    assert done["result"]["spoken"] == "Three flights fit; the cheapest is Frontier at 11:30."

    # Voice flow unchanged: the realtime model is still handed the spoken line to say aloud,
    # never the full written list.
    said = wait_for(lambda: [line for line in spoken(worker) if "cheapest is Frontier" in line])
    assert said
    assert not any("UA 123" in line for line in spoken(worker))
