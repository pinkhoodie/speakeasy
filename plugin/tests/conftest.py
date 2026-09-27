"""Pytest wiring: fake Hermes, temp HERMES_HOME, a VoiceService with a fake live worker, a live server."""
from __future__ import annotations

import shutil

import pytest

from fakes import FakeHermesServer, FakeLiveWorker, FakeTransport, make_home, http  # noqa: F401


@pytest.fixture
def hermes():
    server = FakeHermesServer()
    yield server
    server.close()


@pytest.fixture
def home(hermes):
    path = make_home(hermes.httpd.server_address[1])
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def service(home):
    from speakeasy.service import VoiceService
    workers: list[FakeLiveWorker] = []

    def make_worker(rt, interaction):
        worker = FakeLiveWorker(rt, interaction)
        workers.append(worker)
        return worker

    b = VoiceService(home, notifier=None, start_threads=False, codex_factory=FakeTransport,
               codex_login=lambda binary: (True, "Signed in to Codex."),
               openai_negotiate=lambda key, payload: {"session": {"id": "sess_fake"}, "transport": {"sdp": "v=0\r\n"}},
               openai_worker=make_worker)
    b.settings.patch({"voice": {"provider": "openai"}})
    b.workers = workers  # type: ignore[attr-defined]
    yield b
    b.close()


@pytest.fixture
def server(service):
    from speakeasy.server import SpeakeasyServer
    srv = SpeakeasyServer(service, "127.0.0.1", 0)
    srv.start()
    code = service.devices.new_pairing_code()
    status, paired = http(srv.base_url, "POST", "/voice/pair", {"code": code, "device_name": "Test Mac"})
    assert status == 201
    srv.token = paired["token"]  # type: ignore[attr-defined]
    yield srv
    srv.httpd.shutdown()
    srv.httpd.server_close()
