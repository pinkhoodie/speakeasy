"""Pairing-link handoff: web links keep the code out of the web server's logs, and a link queued by
setup is delivered once the voice platform starts after the user's own restart."""
from __future__ import annotations

import json

from speakeasy import handoff as H
from speakeasy.devices import DeviceStore


def test_web_link_puts_server_and_code_in_the_fragment():
    link = H.web_link("https://box.tail123.ts.net:8795", "123456")
    assert link == "https://speakeasyvoice.ai/pair#server=https%3A%2F%2Fbox.tail123.ts.net%3A8795&code=123456"
    assert "?" not in link  # nothing in the query string, so the page's host never sees it


def test_app_link_is_unchanged():
    assert H.app_link("http://127.0.0.1:8795", "123456") == "speakeasy://pair?server=http%3A%2F%2F127.0.0.1%3A8795&code=123456"


def test_chat_message_mentions_the_download_and_the_expiry():
    text = H.chat_message("https://speakeasyvoice.ai/pair#x")
    assert "download" in text and "30 minutes" in text and "https://speakeasyvoice.ai/pair#x" in text


def test_pending_link_round_trip_and_expiry(tmp_path):
    H.save_pending(tmp_path, "discord:123", now=1000)
    assert H.pending_target(tmp_path, now=1000 + 60) == "discord:123"
    assert H.pending_target(tmp_path, now=1000 + H.PENDING_MAX_AGE_S + 1) == ""
    assert not (tmp_path / "speakeasy" / "pending_link.json").exists()


def test_corrupt_pending_file_is_ignored(tmp_path):
    (tmp_path / "speakeasy").mkdir()
    (tmp_path / "speakeasy" / "pending_link.json").write_text("{not json")
    assert H.pending_target(tmp_path) == ""


def test_deliver_pending_sends_one_fresh_link_and_clears(tmp_path):
    H.save_pending(tmp_path, "telegram")
    store = DeviceStore(tmp_path)
    sent = []
    ok = H.deliver_pending(tmp_path, "https://box.tail123.ts.net:8795",
                           send=lambda target, text: sent.append((target, text)) or True,
                           new_code=lambda: store.new_pairing_code(ttl=H.LINK_TTL_S), sleep=lambda s: None)
    assert ok and len(sent) == 1 and sent[0][0] == "telegram"
    assert "https://speakeasyvoice.ai/pair#server=https%3A%2F%2Fbox.tail123.ts.net%3A8795&code=" in sent[0][1]
    assert H.pending_target(tmp_path) == ""
    # The code in the message actually pairs.
    code = sent[0][1].split("code=")[1][:6]
    assert store.redeem(code, "Mac") is not None


def test_deliver_pending_retries_then_gives_up_and_keeps_the_record(tmp_path):
    H.save_pending(tmp_path, "telegram")
    tries = []
    ok = H.deliver_pending(tmp_path, "http://127.0.0.1:8795", send=lambda t, x: tries.append(t) and False,
                           new_code=lambda: "123456", sleep=lambda s: None, attempts=3)
    assert not ok and len(tries) == 3 and H.pending_target(tmp_path) == "telegram"


def test_nothing_pending_does_nothing(tmp_path):
    called = []
    assert H.deliver_pending(tmp_path, "x", send=lambda *a: called.append(a) or True,
                             new_code=lambda: "1", sleep=lambda s: None) is False
    assert called == []


def test_longer_code_lifetime_for_links_sent_to_chat(tmp_path):
    store = DeviceStore(tmp_path)
    code = store.new_pairing_code(now=0, ttl=H.LINK_TTL_S)
    assert store.redeem(code, "Mac", now=H.LINK_TTL_S - 5) is not None
    code = store.new_pairing_code(now=0)
    assert store.redeem(code, "Mac", now=601) is None  # the 10-minute default is unchanged
