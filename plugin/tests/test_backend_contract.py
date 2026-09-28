"""Every task backend satisfies the TaskBackend contract calls.py drives."""
from speakeasy.backends import Capabilities, TaskBackend
from speakeasy.hermes_api import HermesAPI


def test_hermes_api_is_a_task_backend():
    api = HermesAPI("http://127.0.0.1:1", lambda: "")
    assert isinstance(api, TaskBackend)
    caps = api.capabilities()
    assert isinstance(caps, Capabilities) and caps.kind == "hermes" and caps.chat_delivery and caps.threads
