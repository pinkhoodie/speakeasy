"""Look at this: screen captures, pictures and files coming in from the Mac, the screen toggle,
capture requests, per-call state and Speakeasy's copies of shared files. Real HTTP against the
server; the Mac is the test."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import stat
import threading
import time
import urllib.error
import urllib.request

import pytest

from fakes import SDP, http, wait_for
from speakeasy import attachments as A
from speakeasy.prompt import builder as P

JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01"
PNG_HEAD = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"


def jpeg(size: int = 4096, marker: bytes = b"pixels") -> bytes:
    return JPEG_HEAD + (marker * (size // len(marker) + 1))[:size - len(JPEG_HEAD)]


def open_call(server, key: str, screen: str | None = "ready", **extra):
    body = {"sdp": SDP, **({"screen": screen} if screen is not None else {}), **extra}
    status, session = http(server.base_url, "POST", "/voice/sessions", body, server.token, {"Idempotency-Key": key})
    assert status == 201, session
    return session["interaction_id"]


def upload(server, iid: str, data: bytes, kind: str = "picture", ctype: str = "image/jpeg",
           headers: dict[str, str] | None = None, token: str | None | bool = None):
    req = urllib.request.Request(f"{server.base_url}/voice/interactions/{iid}/attachments", data=data, method="POST")
    req.add_header("Content-Type", ctype)
    if kind:
        req.add_header("X-Speakeasy-Kind", kind)
    if token is not False:
        req.add_header("Authorization", f"Bearer {token or server.token}")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def snapshot(server, iid: str) -> dict:
    status, snap = http(server.base_url, "GET", f"/voice/interactions/{iid}", token=server.token)
    assert status == 200
    return snap


def screen(server, iid: str, body, token: str | None = None):
    return http(server.base_url, "POST", f"/voice/interactions/{iid}/screen", body, token or server.token)


def report(server, iid: str, capture_id: str, body):
    return http(server.base_url, "POST", f"/voice/interactions/{iid}/captures/{capture_id}", body, server.token)


def notes(worker) -> list[str]:
    return [c for k, _, c in worker.sent if k == "session.thinking.append"]


def shared_files(home) -> list:
    return sorted(p for p in (home / "cache" / "speakeasy" / "shared").glob("*/*"))


# -- pictures and files ---------------------------------------------------------------------------

def test_a_picture_waits_in_memory_and_shows_in_the_snapshot(server, service, home):
    iid = open_call(server, "req_pic")
    status, body = upload(server, iid, jpeg())
    assert status == 200 and body["kind"] == "picture" and body["id"].startswith("att_")
    snap = snapshot(server, iid)
    assert snap["attachments"] == [{"id": body["id"], "kind": "picture", "name": None, "app": None, "state": "pending"}]
    assert snap["screen"] == {"on": False, "seq": 0, "declared": "ready"}
    assert snap["captures"] == [] and snap["hold"] is None
    assert not (home / "cache" / "speakeasy" / "shared").exists()  # pictures stay in memory until sent
    worker = service.workers[-1]
    wait_for(lambda: any("1 picture" in n for n in notes(worker)))


def test_a_file_is_saved_under_shared_with_its_name(server, service, home):
    iid = open_call(server, "req_pdf")
    pdf = b"%PDF-1.7\n" + bytes(range(256)) * 8000  # about 2 MB
    status, body = upload(server, iid, pdf, "file", "application/pdf", {"X-Speakeasy-Filename": "Q3%20report.pdf"})
    assert status == 200 and body["kind"] == "file"
    [saved] = shared_files(home)
    assert saved.name == "Q3 report.pdf" and saved.read_bytes() == pdf
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600  # never executable
    assert stat.S_IMODE(saved.parent.stat().st_mode) == 0o700
    assert snapshot(server, iid)["attachments"] == [
        {"id": body["id"], "kind": "file", "name": "Q3 report.pdf", "app": None, "state": "pending"}]
    worker = service.workers[-1]
    wait_for(lambda: any("1 file" in n for n in notes(worker)))
    # The voice gets counts only: never the name.
    assert not any("Q3 report" in c for _, _, c in worker.sent)


def test_wrong_image_bytes_are_refused(server):
    iid = open_call(server, "req_415")
    png = PNG_HEAD + b"\x00" * 64
    assert upload(server, iid, png, ctype="image/jpeg")[0] == 415
    assert upload(server, iid, b"GIF89a" + b"\x00" * 64, ctype="image/gif")[0] == 415
    assert upload(server, iid, png, ctype="image/png")[0] == 200
    assert len(snapshot(server, iid)["attachments"]) == 1


@pytest.mark.parametrize("kind,cap", [("picture", A.MAX_IMAGE_BYTES), ("file", A.MAX_FILE_BYTES)])
def test_an_oversized_upload_is_refused_before_its_body_is_read(server, kind, cap):
    iid = open_call(server, f"req_413_{kind}")
    head = (f"POST /voice/interactions/{iid}/attachments HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            f"Authorization: Bearer {server.token}\r\nContent-Type: image/jpeg\r\nX-Speakeasy-Kind: {kind}\r\n"
            f"X-Speakeasy-Filename: big.bin\r\nContent-Length: {cap + 1}\r\n\r\n").encode()
    started = time.monotonic()
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
        sock.sendall(head + JPEG_HEAD + b"0" * 1000)  # a sliver of what was declared, then nothing
        reply = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            reply += chunk
    assert reply.startswith(b"HTTP/1.0 413") and b'"reason":"too_large"' in reply
    assert time.monotonic() - started < 4  # answered without waiting for the declared body
    assert snapshot(server, iid)["attachments"] == []


def test_a_fourth_attachment_is_refused(server):
    iid = open_call(server, "req_four")
    for i in range(2):
        assert upload(server, iid, jpeg(marker=bytes([65 + i])))[0] == 200
    assert upload(server, iid, b"a,b\n1,2\n", "file", "text/csv", {"X-Speakeasy-Filename": "data.csv"})[0] == 200
    status, body = upload(server, iid, jpeg(marker=b"Z"))
    assert status == 409 and body["reason"] == "too_many"
    status, body = upload(server, iid, b"log", "file", "text/plain", {"X-Speakeasy-Filename": "x.log"})
    assert status == 409 and body["reason"] == "too_many"


def test_a_picture_must_leave_room_for_a_screen_capture(server):
    ready = open_call(server, "req_budget_ready")
    two_mb = 2_000_000
    assert upload(server, ready, jpeg(two_mb, b"A"))[0] == 200
    assert upload(server, ready, jpeg(two_mb, b"B"))[0] == 200  # 4 MB + room for a 2.5 MB capture = 6.5 MB
    status, body = upload(server, ready, jpeg(100_000, b"C"))
    assert status == 409 and body["reason"] == "too_large"
    # Files never count against the image budget.
    assert upload(server, ready, b"notes", "file", "text/plain", {"X-Speakeasy-Filename": "n.txt"})[0] == 200
    # A call that can't capture keeps no room for one.
    other = open_call(server, "req_budget_none", screen=None)
    for marker in (b"A", b"B", b"C"):
        assert upload(server, other, jpeg(two_mb if marker != b"C" else 100_000, marker))[0] == 200


def test_bad_upload_headers(server):
    iid = open_call(server, "req_headers")
    assert upload(server, iid, jpeg(), kind="")[0] == 400                      # no kind
    assert upload(server, iid, jpeg(), kind="video")[0] == 400
    assert upload(server, iid, jpeg(), "screen")[0] == 400                     # a capture needs its id
    assert upload(server, iid, jpeg(), headers={"X-Speakeasy-Capture-Id": "cap_1"})[0] == 400
    assert upload(server, iid, b"text", "file", "text/plain")[0] == 400        # a file needs its name
    assert upload(server, iid, b"", "file", "text/plain", {"X-Speakeasy-Filename": "e.txt"})[0] == 400
    assert upload(server, "vi_nope", jpeg())[0] == 404


def test_uploads_and_controls_need_the_device_token(server):
    iid = open_call(server, "req_auth")
    assert upload(server, iid, jpeg(), token=False)[0] == 401
    assert upload(server, iid, jpeg(), token="wrong-token")[0] == 401
    assert screen(server, iid, {"on": True, "seq": 1}, token="wrong-token")[0] == 401
    assert http(server.base_url, "POST", f"/voice/interactions/{iid}/captures/cap_x", {"status": "capturing"})[0] == 401
    assert http(server.base_url, "DELETE", f"/voice/interactions/{iid}/attachments/att_x", None, "wrong")[0] == 401
    assert snapshot(server, iid)["attachments"] == []


def test_filenames_are_sanitized_and_stay_inside_shared(server, home):
    iid = open_call(server, "req_names")
    shared = home / "cache" / "speakeasy" / "shared"
    for raw, want in (("../../etc/passwd", "passwd"), ("%2E%2E%2F%2E%2E%2Fevil.sh", "evil.sh"),
                      ("..\\..\\win.ini", "win.ini")):
        status, _ = upload(server, iid, raw.encode(), "file", "text/plain", {"X-Speakeasy-Filename": raw})
        assert status == 200
        saved = [p for p in shared_files(home) if p.read_bytes() == raw.encode()]
        assert [p.name for p in saved] == [want]
        assert saved[0].resolve().parent.parent == shared.resolve()
        assert not saved[0].stat().st_mode & 0o111
    assert not (home.parent / "etc").exists()


def test_safe_filename_and_app():
    assert A.safe_filename("\u202egnp.exe") == "gnp.exe"          # no direction overrides
    assert A.safe_filename(".bashrc") == "bashrc"                 # never hidden
    assert A.safe_filename("..") == "file" and A.safe_filename("") == "file"
    assert A.safe_filename("x\x00y\n.txt") == "xy.txt"
    assert A.safe_filename("C:\\Users\\me\\doc.txt") == "doc.txt"
    assert A.safe_filename("a:b?.md") == "a_b_.md"
    long = A.safe_filename("a" * 300 + ".pdf")
    assert len(long) <= A.MAX_NAME_CHARS and long.endswith(".pdf")
    assert len(A.safe_filename("é" * 300 + ".pdf").encode()) <= A.MAX_NAME_BYTES
    assert A.safe_filename("na%C3%AFve%20r%C3%A9sum%C3%A9.pdf") == "naïve résumé.pdf"
    assert A.safe_filename("résumé.pdf".encode().decode("latin-1")) == "résumé.pdf"  # raw UTF-8 in a header
    assert A.safe_app("Xcode\n") == "Xcode" and A.safe_app("") is None and A.safe_app(None) is None
    assert len(A.safe_app("x" * 500) or "") == A.MAX_APP_CHARS


def test_removing_a_pending_attachment(server, service, home):
    iid = open_call(server, "req_remove")
    _, picture = upload(server, iid, jpeg())
    _, file = upload(server, iid, b"draft", "file", "text/plain", {"X-Speakeasy-Filename": "draft.txt"})
    url = f"/voice/interactions/{iid}/attachments"
    status, body = http(server.base_url, "DELETE", f"{url}/{picture['id']}", None, server.token)
    assert status == 200 and body == {"id": picture["id"], "removed": True}
    assert [a["id"] for a in snapshot(server, iid)["attachments"]] == [file["id"]]
    assert http(server.base_url, "DELETE", f"{url}/{picture['id']}", None, server.token)[0] == 404
    assert http(server.base_url, "DELETE", f"{url}/att_unknown", None, server.token)[0] == 404
    # Removing a file deletes Speakeasy's copy too.
    assert http(server.base_url, "DELETE", f"{url}/{file['id']}", None, server.token)[0] == 200
    assert shared_files(home) == []
    worker = service.workers[-1]
    wait_for(lambda: any("nothing is waiting" in n for n in notes(worker)))


def test_an_attachment_already_sent_cant_be_removed(server, service):
    iid = open_call(server, "req_sent")
    _, picture = upload(server, iid, jpeg())
    worker = service.workers[-1]
    assert [a.id for a in service.interaction(iid).attachments.bind_pending("call_1")] == [picture["id"]]
    assert snapshot(server, iid)["attachments"][0]["state"] == "sending"
    taken = worker.take_attachments("call_1", "se_sent")
    assert [a.data for a in taken] == [jpeg()]
    status, body = http(server.base_url, "DELETE", f"/voice/interactions/{iid}/attachments/{picture['id']}", None,
                        server.token)
    assert status == 409 and body["reason"] == "sent"
    assert snapshot(server, iid)["attachments"] == []


def test_binding_releasing_and_taking():
    shared = A.CallAttachments("ready")
    first = shared.add("picture", mime="image/jpeg", size=10, data=b"1" * 10)
    assert shared.bind_pending("d1") == [first]
    second = shared.add("file", mime="text/plain", size=3, path="/x/y.txt", name="y.txt")
    assert shared.pending() == [second]                    # a later drop waits for the next request
    assert shared.release("d1") == [first]                 # the handoff needed nothing: back to pending
    assert shared.pending() == [first, second]
    assert shared.bind_pending("d2") == [first, second]
    taken = shared.take_bound("d2")
    assert taken[0].data == b"1" * 10                      # handed over with its bytes ...
    assert shared.pending() == [] and shared.take_bound("d2") == []
    with pytest.raises(A.Refused) as refused:
        shared.remove(first.id)
    assert refused.value.status == 409                     # ... and kept only as sent metadata


# -- the screen toggle ----------------------------------------------------------------------------

def test_the_screen_toggle_flips_and_notes_each_change(server, service):
    iid = open_call(server, "req_toggle")
    worker = service.workers[-1]
    assert screen(server, iid, {"on": True, "seq": 1}) == (200, {"on": True, "seq": 1})
    assert snapshot(server, iid)["screen"] == {"on": True, "seq": 1, "declared": "ready"}
    wait_for(lambda: P.SCREEN_ON_NOTE in notes(worker))
    assert screen(server, iid, {"on": False, "seq": 2}) == (200, {"on": False, "seq": 2})
    wait_for(lambda: P.SCREEN_OFF_NOTE in notes(worker))
    # A late request with an older seq changes nothing.
    assert screen(server, iid, {"on": True, "seq": 1}) == (200, {"on": False, "seq": 2})
    assert screen(server, iid, {"on": False, "seq": 3}) == (200, {"on": False, "seq": 3})  # no change, no note
    time.sleep(0.3)
    assert notes(worker).count(P.SCREEN_ON_NOTE) == 1 and notes(worker).count(P.SCREEN_OFF_NOTE) == 1
    assert snapshot(server, iid)["screen"]["on"] is False


def test_the_screen_body_must_be_exact(server):
    iid = open_call(server, "req_toggle_bad")
    for body in ({"on": "yes", "seq": 1}, {"on": True}, {"on": True, "seq": 1, "extra": 1}, {"on": True, "seq": True},
                 {"on": True, "seq": -1}, {"on": True, "seq": 1.5}, {"seq": 1}):
        assert screen(server, iid, body)[0] == 400, body
    assert screen(server, "vi_nope", {"on": True, "seq": 1})[0] == 404
    assert snapshot(server, iid)["screen"]["on"] is False


def test_sharing_needs_a_call_that_can_share(server):
    blocked = open_call(server, "req_noperm", screen="no_permission")
    status, body = screen(server, blocked, {"on": True, "seq": 1})
    assert status == 409 and body["reason"] == "permission"
    undeclared = open_call(server, "req_undeclared", screen=None)
    status, body = screen(server, undeclared, {"on": True, "seq": 1})
    assert status == 409 and body["reason"] == "not_declared"
    assert screen(server, undeclared, {"on": False, "seq": 2})[0] == 200
    assert snapshot(server, undeclared)["screen"] == {"on": False, "seq": 2, "declared": None}


def test_sessions_reject_an_unknown_screen_value(server):
    for value in ("maybe", True, 1):
        status, _ = http(server.base_url, "POST", "/voice/sessions", {"sdp": SDP, "screen": value}, server.token,
                         {"Idempotency-Key": f"req_screen_{value}"})
        assert status == 400


def test_notes_wait_for_a_connecting_call(server, service):
    iid = open_call(server, "req_connecting")
    worker = service.workers[-1]
    worker.connected = False
    assert screen(server, iid, {"on": True, "seq": 1})[0] == 200
    assert screen(server, iid, {"on": False, "seq": 2})[0] == 200
    assert screen(server, iid, {"on": True, "seq": 3})[0] == 200
    time.sleep(0.3)
    assert notes(worker) == []
    worker.connected = True
    wait_for(lambda: P.SCREEN_ON_NOTE in notes(worker))
    time.sleep(0.3)
    assert notes(worker) == [P.SCREEN_ON_NOTE]  # the on-off-on while connecting is one change


# -- capture requests -----------------------------------------------------------------------------

def test_a_capture_request_is_answered_by_one_upload(server, service):
    iid = open_call(server, "req_capture")
    screen(server, iid, {"on": True, "seq": 1})
    worker = service.workers[-1]
    interaction = service.interaction(iid)
    capture_id = worker.request_capture("call_look")
    assert capture_id and capture_id.startswith("cap_")
    assert worker.request_capture("call_look") == capture_id  # asking again keeps the one request
    assert [p for _, k, p in interaction.feed.ring if k == "capture"] == [{"capture_id": capture_id}]
    assert snapshot(server, iid)["captures"] == [{"capture_id": capture_id}]
    assert report(server, iid, capture_id, {"status": "capturing"}) == (200, {"capture_id": capture_id,
                                                                              "status": "capturing"})
    assert report(server, iid, capture_id, {"status": "uploading"})[0] == 200
    shot = jpeg(50_000, b"window")
    headers = {"X-Speakeasy-Capture-Id": capture_id, "X-Speakeasy-App": "Xcode"}
    status, first = upload(server, iid, shot, "screen", headers=headers)
    assert status == 200 and first["kind"] == "screen"
    status, again = upload(server, iid, shot, "screen", headers=headers)
    assert status == 200 and again == first  # the same request again: the same attachment
    assert snapshot(server, iid)["captures"] == []
    assert snapshot(server, iid)["attachments"] == []  # a capture is never a pending attachment
    result = asyncio.run(interaction.attachments.wait_capture(capture_id, 1, 1))
    assert result.reason is None and result.attachment.data == shot and result.attachment.app == "Xcode"
    assert interaction.attachments.wait_capture_blocking(capture_id, 0.1).reason == "taken"  # handed over once
    assert report(server, iid, capture_id, {"status": "capturing"})[0] == 410
    assert upload(server, iid, shot, "screen", headers=headers) == (200, first)


def test_a_closed_capture_request_is_refused_and_nothing_is_kept(server, service):
    iid = open_call(server, "req_capture_closed")
    screen(server, iid, {"on": True, "seq": 1})
    worker = service.workers[-1]
    capture_id = worker.request_capture("call_look")
    screen(server, iid, {"on": False, "seq": 2})  # turning sharing off closes it
    status, body = report(server, iid, capture_id, {"status": "capturing"})
    assert status == 410 and body["reason"] == "closed"
    status, body = upload(server, iid, jpeg(), "screen", headers={"X-Speakeasy-Capture-Id": capture_id})
    assert status == 410
    assert service.interaction(iid).attachments.wait_capture_blocking(capture_id, 0.1).reason == "sharing_off"
    assert report(server, iid, "cap_0000000000000000", {"status": "capturing"})[0] == 410
    assert snapshot(server, iid)["captures"] == []


def test_a_failed_capture_closes_with_its_reason(server, service):
    iid = open_call(server, "req_capture_failed")
    screen(server, iid, {"on": True, "seq": 1})
    capture_id = service.workers[-1].request_capture("call_look")
    for body in ({"status": "failed"}, {"status": "failed", "reason": "bogus"},
                 {"status": "capturing", "reason": "blank"}, {"status": "done"}, {"status": "failed",
                                                                                  "reason": "blank", "x": 1}):
        assert report(server, iid, capture_id, body)[0] == 400, body
    assert report(server, iid, capture_id, {"status": "failed", "reason": "secure_input"})[0] == 200
    assert service.interaction(iid).attachments.wait_capture_blocking(capture_id, 0.1).reason == "secure_input"
    assert report(server, iid, capture_id, {"status": "capturing"})[0] == 410
    assert snapshot(server, iid)["captures"] == []


def test_no_capture_requests_unless_a_ready_call_is_sharing(server, service):
    undeclared = open_call(server, "req_cap_undeclared", screen=None)
    assert service.workers[-1].request_capture("call_1") is None
    ready = open_call(server, "req_cap_off")
    worker = service.workers[-1]
    assert worker.request_capture("call_1") is None  # sharing is off
    assert not [k for _, k, _ in service.interaction(ready).feed.ring if k == "capture"]
    assert snapshot(server, undeclared)["captures"] == []


def test_waiting_for_a_capture_times_out_and_uploading_extends_it():
    shared = A.CallAttachments("ready")
    shared.set_screen(True, 1)
    late = shared.open_capture("d1")
    assert shared.wait_capture_blocking(late, 0.2, 2).reason == "timeout"
    assert not shared.capture_status(late, "capturing")  # closed: the Mac never captures for it now
    with pytest.raises(A.Refused):
        shared.deliver_capture(late, data=b"x", mime="image/jpeg")
    slow = shared.open_capture("d2")
    assert shared.capture_status(slow, "uploading")
    threading.Timer(0.5, lambda: shared.deliver_capture(slow, data=b"shot", mime="image/jpeg")).start()
    result = shared.wait_capture_blocking(slow, 0.2, 3)
    assert result.attachment is not None and result.attachment.data == b"shot"


def test_an_unanswered_capture_request_expires(monkeypatch):
    monkeypatch.setattr(A, "CAPTURE_OPEN_TTL_S", 0.1)
    shared = A.CallAttachments("ready")
    shared.set_screen(True, 1)
    capture_id = shared.open_capture("d1")
    time.sleep(0.2)
    assert shared.open_captures() == [] and not shared.capture_status(capture_id, "capturing")
    assert shared.open_capture("d1") != capture_id  # the handoff may ask again


# -- the call's end and resume --------------------------------------------------------------------

def test_a_paused_call_refuses_uploads_and_a_resume_starts_fresh(server, service):
    iid = open_call(server, "req_pause")
    assert upload(server, iid, jpeg())[0] == 200
    screen(server, iid, {"on": True, "seq": 1})
    capture_id = service.workers[-1].request_capture("call_look")
    assert http(server.base_url, "POST", f"/voice/interactions/{iid}/pause", {}, server.token)[0] == 200
    status, body = upload(server, iid, jpeg(marker=b"late"))
    assert status == 409 and body["reason"] == "ended"
    assert screen(server, iid, {"on": True, "seq": 2})[0] == 409
    assert report(server, iid, capture_id, {"status": "capturing"})[0] == 410
    old = snapshot(server, iid)
    assert old["attachments"] == [] and old["captures"] == [] and old["screen"]["on"] is False
    status, resumed = http(server.base_url, "POST", "/voice/sessions", {"sdp": SDP, "resume_from": iid}, server.token,
                           {"Idempotency-Key": "req_pause_resume"})
    assert status == 201
    fresh = snapshot(server, resumed["interaction_id"])
    assert fresh["screen"] == {"on": False, "seq": 0, "declared": "ready"}  # the declaration carries over
    assert fresh["attachments"] == [] and fresh["captures"] == []
    assert upload(server, resumed["interaction_id"], jpeg())[0] == 200  # the Mac re-sends


def test_a_resume_can_change_the_declaration(server):
    iid = open_call(server, "req_redeclare")
    assert http(server.base_url, "POST", f"/voice/interactions/{iid}/pause", {}, server.token)[0] == 200
    status, resumed = http(server.base_url, "POST", "/voice/sessions",
                           {"sdp": SDP, "resume_from": iid, "screen": "no_permission"}, server.token,
                           {"Idempotency-Key": "req_redeclare_resume"})
    assert status == 201
    assert snapshot(server, resumed["interaction_id"])["screen"]["declared"] == "no_permission"


def test_call_end_drops_what_was_only_waiting(server, service, home):
    iid = open_call(server, "req_end")
    worker = service.workers[-1]
    _, kept = upload(server, iid, b"kept", "file", "text/plain", {"X-Speakeasy-Filename": "kept.txt"})
    service.interaction(iid).attachments.bind_pending("call_1")  # on its way with a request
    assert upload(server, iid, b"unsent", "file", "text/plain", {"X-Speakeasy-Filename": "unsent.txt"})[0] == 200
    worker.call_closed()
    assert [p.name for p in shared_files(home)] == ["kept.txt"]  # the unsent copy is gone
    assert [a.id for a in service.interaction(iid).attachments.take_bound("call_1")] == [kept["id"]]
    status, body = upload(server, iid, jpeg())
    assert status == 409 and body["reason"] == "ended"


# -- Speakeasy's copies: cleared tasks, the weekly prune ------------------------------------------

def test_clearing_a_task_deletes_its_shared_files(server, service, home):
    iid = open_call(server, "req_clear")
    worker = service.workers[-1]
    _, file = upload(server, iid, b"%PDF-1.7 quarterly", "file", "application/pdf",
                     {"X-Speakeasy-Filename": "report.pdf"})
    service.interaction(iid).attachments.bind_pending("call_1")
    service.store.reserve_run("se_clear", iid, "call_1", 1)
    [sent] = worker.take_attachments("call_1", "se_clear")
    assert service.store.shared_files("se_clear") == [sent.path]
    # The same copy recorded by a second task, and pending in this call, outlives the first clear.
    service.store.reserve_run("se_other", iid, "call_2", 1)
    service.store.add_shared_files("se_other", [sent.path])
    for key, run_id in (("se_clear", "run_clear"), ("se_other", "run_other")):
        service.store.update_run(key, run_id, "completed")
    status, body = http(server.base_url, "POST", "/voice/tasks/dismiss", {"run_ids": ["run_clear"]}, server.token)
    assert status == 200 and body["dismissed"] == ["run_clear"]
    assert os.path.exists(sent.path)
    upload(server, iid, b"%PDF-1.7 quarterly", "file", "application/pdf", {"X-Speakeasy-Filename": "report.pdf"})
    http(server.base_url, "POST", "/voice/tasks/dismiss", {"run_ids": ["run_other"]}, server.token)
    assert os.path.exists(sent.path)  # still waiting on the panel for the next request
    pending = service.interaction(iid).attachments.pending()[0]
    http(server.base_url, "DELETE", f"/voice/interactions/{iid}/attachments/{pending.id}", None, server.token)
    assert not os.path.exists(sent.path) and shared_files(home) == []
    assert file["id"] == sent.id


def test_the_idle_loop_prunes_copies_older_than_a_week(service, monkeypatch):
    monkeypatch.setattr(service, "_check_image_support_soon", lambda: None)  # no Hermes model lookups here
    old = service.shared.save(b"old notes", "old.txt")
    fresh = service.shared.save(b"new notes", "new.txt")
    week_ago = time.time() - A.SHARED_KEEP_S - 3600
    os.utime(old, (week_ago, week_ago))
    (old.parent / ".abandoned.part").write_bytes(b"x")
    os.utime(old.parent / ".abandoned.part", (week_ago, week_ago))
    loop = threading.Thread(target=service._idle_loop, daemon=True)
    loop.start()  # its first pass runs at startup
    wait_for(lambda: not old.exists())
    service._stop.set()
    loop.join(5)
    assert not old.parent.exists() and fresh.exists()
    # Sharing a file again restarts its week.
    os.utime(fresh, (week_ago, week_ago))
    assert service.shared.save(b"new notes", "new.txt") == fresh
    assert service.prune_shared() == 0 and fresh.exists()


def test_shared_folder_deletes_only_its_own_copies(tmp_path):
    folder = A.SharedFolder(tmp_path)
    outside = tmp_path / "keep.txt"
    outside.write_text("mine")
    assert folder.delete([str(outside), str(tmp_path / "cache" / "speakeasy" / "shared" / "x.txt")]) == 0
    assert outside.exists()
    saved = folder.save(b"data", "../../keep.txt")
    assert saved.name == "keep.txt" and saved.parent.parent == folder.root
    assert folder.delete([str(saved)]) == 1 and outside.exists()


# -- privacy --------------------------------------------------------------------------------------

def test_logs_never_carry_bytes_or_names(server, service, caplog):
    caplog.set_level(logging.DEBUG)
    iid = open_call(server, "req_logs")
    screen(server, iid, {"on": True, "seq": 1})
    picture = jpeg(30_000, b"SECRET-PIXELS-7731")
    document = b"%PDF-1.7 SECRET-CONTRACT-4410 " * 500
    assert upload(server, iid, picture)[0] == 200
    assert upload(server, iid, document, "file", "application/pdf",
                  {"X-Speakeasy-Filename": "divorce%20papers.pdf"})[0] == 200
    capture_id = service.workers[-1].request_capture("call_look")
    shot = jpeg(30_000, b"SECRET-WINDOW-0915")
    assert upload(server, iid, shot, "screen", headers={"X-Speakeasy-Capture-Id": capture_id})[0] == 200
    assert upload(server, iid, PNG_HEAD + b"SECRET-PNG-2207", ctype="image/jpeg")[0] == 415
    logged = caplog.text + "".join(repr(r.args) for r in caplog.records)
    assert "shared on" in logged  # uploads are logged ...
    for secret in (b"SECRET-PIXELS-7731", b"SECRET-CONTRACT-4410", b"SECRET-WINDOW-0915", b"SECRET-PNG-2207"):
        assert secret.decode() not in logged  # ... never their bytes
    for data in (picture, document, shot):
        assert base64.b64encode(data)[:40].decode() not in logged
        assert base64.b64encode(data[100:400]).decode()[:40] not in logged
    assert "divorce" not in logged
    assert "SECRET" not in repr(service.interaction(iid).attachments.pending())  # nor in a repr
