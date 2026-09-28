"""Every task backend satisfies the TaskBackend contract calls.py drives."""
from speakeasy.backends import Capabilities, TaskBackend
from speakeasy.hermes_api import HermesAPI


def test_hermes_api_is_a_task_backend():
    api = HermesAPI("http://127.0.0.1:1", lambda: "")
    assert isinstance(api, TaskBackend)
    caps = api.capabilities()
    assert isinstance(caps, Capabilities) and caps.kind == "hermes" and caps.chat_delivery and caps.threads


def test_claude_code_backend_is_a_task_backend(tmp_path):
    from speakeasy.backends.claude_code import ClaudeCodeBackend
    backend = ClaudeCodeBackend(tmp_path, claude_bin=tmp_path / "claude")
    assert isinstance(backend, TaskBackend)
    caps = backend.capabilities()
    assert isinstance(caps, Capabilities) and caps.kind == "claude_code" and caps.display_name == "Claude Code"
    assert caps.steer and caps.approvals and caps.file_changes and caps.needs_workspace
    assert not (caps.chat_delivery or caps.threads or caps.conversation_continuity or caps.email_drafts
                or caps.daily_brief)
    assert backend.session_messages("00000000-0000-0000-0000-000000000000") == []
