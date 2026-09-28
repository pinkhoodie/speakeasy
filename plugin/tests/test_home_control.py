"""Instant home control: parsing, matching Home Assistant devices, the REST client against a fake
Home Assistant, the opt-in switch, and the voice wiring (handled here, or on to Hermes unchanged)."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from fakes import SDP, http, wait_for
from speakeasy import cli
from speakeasy import home_control as HC
from speakeasy import settings as S

TOKEN = "fake-ha-token-for-tests"

STATES = [
    {"entity_id": "light.kitchen_main", "state": "on", "attributes": {"friendly_name": "Ceiling Lights", "brightness": 255}},
    {"entity_id": "light.kitchen_island", "state": "off", "attributes": {"friendly_name": "Kitchen Island"}},
    {"entity_id": "light.porch", "state": "off", "attributes": {"friendly_name": "Porch Light"}},
    {"entity_id": "light.desk_lamp", "state": "off", "attributes": {"friendly_name": "Desk Lamp"}},
    {"entity_id": "light.hall", "state": "unavailable", "attributes": {"friendly_name": "Hall Light"}},
    {"entity_id": "climate.upstairs", "state": "heat", "attributes": {
        "friendly_name": "Upstairs Thermostat", "temperature": 68, "current_temperature": 66.5,
        "hvac_modes": ["off", "heat", "cool"], "min_temp": 50, "max_temp": 90}},
    {"entity_id": "climate.downstairs", "state": "cool", "attributes": {
        "friendly_name": "Downstairs Thermostat", "temperature": 72, "hvac_modes": ["off", "heat", "cool"],
        "min_temp": 50, "max_temp": 90}},
    {"entity_id": "lock.front_door", "state": "locked", "attributes": {"friendly_name": "Front Door"}},
    {"entity_id": "cover.garage", "state": "closed", "attributes": {"friendly_name": "Garage Door"}},
]
AREAS = {"light.kitchen_main": "Kitchen", "light.kitchen_island": "Kitchen", "light.desk_lamp": "Office",
         "climate.upstairs": "Bedroom"}


# -- a fake Home Assistant ------------------------------------------------------------------------

class FakeHA:
    def __init__(self, states=None, status=200, delay=0.0):
        self.states, self.status, self.delay = states if states is not None else STATES, status, delay
        self.calls: list[tuple[str, str, dict]] = []
        self.reads = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _auth(self):
                if outer.delay:
                    time.sleep(outer.delay)
                if self.headers.get("Authorization") != f"Bearer {TOKEN}" or outer.status == 401:
                    self._reply(401, {"message": "unauthorized"})
                    return False
                return True

            def do_GET(self):
                if not self._auth():
                    return
                if self.path == "/api/states":
                    outer.reads += 1
                    self._reply(200, outer.states)
                else:
                    self._reply(404, {})

            def do_POST(self):
                if not self._auth():
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path == "/api/template":
                    ids = body["variables"]["ids"]
                    self._reply(200, [AREAS.get(e, "") for e in ids])
                    return
                parts = self.path.split("/")
                if outer.status != 200:
                    self._reply(outer.status, {"message": "nope"})
                    return
                outer.calls.append((parts[3], parts[4], body))
                self._reply(200, [])

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def ha():
    fake = FakeHA()
    yield fake
    fake.close()


def control(ha, enabled=True, timeout=2.0):
    return HC.HomeControl(lambda: enabled, lambda: (ha.url, TOKEN),
                          client_factory=lambda url, token: HC.HomeAssistantClient(url, token, timeout))


# -- parsing ------------------------------------------------------------------------------------

@pytest.mark.parametrize("said, domain, op, target, value, mode", [
    ("Turn off the kitchen lights.", "light", "off", ("kitchen",), None, None),
    ("turn the porch light on please", "light", "on", ("porch",), None, None),
    ("Okay. Switch on the desk lamp.", "light", "on", ("desk",), None, None),
    ("dim the kitchen lights to 30%", "light", "brightness", ("kitchen",), 30, None),
    ("set the kitchen lights to fifty percent", "light", "brightness", ("kitchen",), 50, None),
    ("set the thermostat to 72", "climate", "temperature", (), 72, None),
    ("set the upstairs thermostat to seventy-two degrees", "climate", "temperature", ("upstairs",), 72, None),
    ("set the downstairs thermostat to cool", "climate", "mode", ("downstairs",), None, "cool"),
    ("turn the heat on", "climate", "mode", (), None, "heat"),
    ("turn off the AC", "climate", "mode", (), None, "off"),
    ("what's the thermostat set to?", "climate", "query", (), None, None),
])
def test_parses_clear_light_and_thermostat_commands(said, domain, op, target, value, mode):
    intent = HC.parse(said)
    assert intent is not None, said
    assert (intent.domain, intent.op, intent.target, intent.value, intent.mode) == (domain, op, target, value, mode)


def test_all_lights_off_and_pronouns():
    for said in ("turn off all the lights", "all lights off", "lights off everywhere"):
        intent = HC.parse(said)
        assert intent and intent.op == "off" and intent.everything and not intent.target, said
    assert HC.parse("what's it set to").pronoun
    assert HC.parse("set it to 70 degrees").pronoun


@pytest.mark.parametrize("said", [
    "lock the front door", "unlock the door", "open the garage", "close the blinds", "arm the alarm",
    "turn up the music", "turn on the TV", "turn on the fan", "run the bedtime script", "activate movie scene",
    "turn off the kitchen lights and the TV", "turn off the lights in 10 minutes", "don't turn off the lights",
    "turn on all the lights", "turn off everything", "turn on the thermostat", "set the kitchen to 70",
    "set the lights to blue", "how warm is it outside", "Turn off the lights. Then lock up.",
    "turn off the thermostat lights", "set all the thermostats to 70",
])
def test_anything_else_is_not_ours(said):
    assert HC.parse(said) is None


# -- resolving against real-shaped states -----------------------------------------------------

def test_area_name_and_prefix_matching(ha):
    hc = control(ha)
    devices = hc.devices(hc._client())
    lights = [d for d in devices if d.domain == "light"]
    assert {d.entity_id for d in HC.match(("kitchen",), lights)[0]} == {"light.kitchen_main", "light.kitchen_island"}
    assert [d.entity_id for d in HC.match(("porch",), lights)[0]] == ["light.porch"]
    assert [d.entity_id for d in HC.match(("office",), lights)[0]] == ["light.desk_lamp"]
    assert HC.match(("garden",), lights) is None
    assert not any(d.domain in {"lock", "cover"} for d in devices)


def test_turns_off_an_area_and_speaks_only_after_200(ha):
    reply = control(ha).respond("turn off the kitchen lights")
    assert reply.ok and reply.spoken == "Kitchen lights off."
    assert ha.calls == [("light", "turn_off", {"entity_id": ["light.kitchen_main", "light.kitchen_island"]})]


def test_brightness_and_all_lights_off(ha):
    hc = control(ha)
    assert hc.respond("set the porch light to 40 percent").spoken == "Porch Light at 40 percent."
    assert ha.calls[-1] == ("light", "turn_on", {"entity_id": "light.porch", "brightness_pct": 40})
    reply = hc.respond("turn off all the lights")
    assert reply.ok and reply.spoken == "All the lights off."
    # the offline one is skipped, every live light is included
    assert set(ha.calls[-1][2]["entity_id"]) == {"light.kitchen_main", "light.kitchen_island", "light.porch",
                                                   "light.desk_lamp"}


def test_thermostat_set_mode_query_and_remembered_room(ha):
    hc = control(ha)
    assert hc.respond("set the thermostat to 70") is None  # two thermostats, none used yet: Hermes asks
    assert hc.respond("set the upstairs thermostat to 70").spoken == "Upstairs Thermostat set to 70."
    assert ha.calls[-1] == ("climate", "set_temperature", {"entity_id": "climate.upstairs", "temperature": 70.0})
    assert hc.respond("set the thermostat to 71").spoken == "Upstairs Thermostat set to 71."  # last used
    assert hc.respond("set the downstairs thermostat to heat").spoken == "Downstairs Thermostat set to heat."
    assert ha.calls[-1] == ("climate", "set_hvac_mode", {"entity_id": "climate.downstairs", "hvac_mode": "heat"})
    assert "set to 72" in hc.respond("what's it set to?").spoken
    assert hc.respond("set the bedroom thermostat to 99").ok is False  # out of the device's range
    assert len([c for c in ha.calls if c[1] == "set_temperature"]) == 2


def test_unknown_or_offline_devices(ha):
    hc = control(ha)
    assert hc.respond("turn off the garden lights") is None
    reply = hc.respond("turn on the hall light")
    assert reply.ok is False and "offline" in reply.spoken
    assert ha.calls == []


def test_off_or_uncredentialed_does_nothing(ha):
    assert control(ha, enabled=False).respond("turn off the kitchen lights") is None
    hc = HC.HomeControl(lambda: True, lambda: None)
    assert hc.respond("turn off the kitchen lights") is None
    assert ha.reads == 0


def test_device_list_is_cached_briefly(ha):
    now = [100.0]
    hc = HC.HomeControl(lambda: True, lambda: (ha.url, TOKEN), clock=lambda: now[0])
    hc.devices(hc._client())
    hc.devices(hc._client())
    assert ha.reads == 1
    now[0] += HC.CACHE_S + 1
    hc.devices(hc._client())
    assert ha.reads == 2


# -- failures -----------------------------------------------------------------------------------

def test_401_says_so_and_offers_hermes():
    fake = FakeHA(status=401)
    try:
        reply = control(fake).respond("turn off the kitchen lights", "Hermes")
    finally:
        fake.close()
    assert reply.ok is False and "access token" in reply.spoken and "hand it to Hermes" in reply.spoken
    assert TOKEN not in reply.spoken


def test_service_error_never_claims_success(ha):
    hc = control(ha)
    hc.devices(hc._client())
    ha.status = 500
    reply = hc.respond("turn off the porch light")
    assert reply.ok is False and "nothing changed" in reply.spoken


def test_timeout_and_unreachable():
    slow = FakeHA(delay=1.0)
    try:
        reply = control(slow, timeout=0.3).respond("turn off the porch light")
    finally:
        slow.close()
    assert reply.ok is False and ("reach" in reply.spoken or "in time" in reply.spoken)
    dead = HC.HomeControl(lambda: True, lambda: ("http://127.0.0.1:9", TOKEN))
    reply = dead.respond("turn off the porch light")
    assert reply.ok is False and "couldn't reach" in reply.spoken


def test_client_never_calls_other_domains(ha):
    client = HC.HomeAssistantClient(ha.url, TOKEN)
    for domain, service in (("lock", "unlock"), ("cover", "open_cover"), ("script", "turn_on"), ("light", "toggle")):
        with pytest.raises(ValueError):
            client.call_service(domain, service, {})
    assert TOKEN not in repr(client)


# -- turning it on ------------------------------------------------------------------------------

def _only_this_env(monkeypatch):
    """Credentials come only from the temp profile's .env, never this machine's real Hermes."""
    monkeypatch.setattr(S, "hermes_secret", lambda home, name: S.read_env_file(home / ".env").get(name, ""))


def test_enable_refuses_without_home_assistant_credentials(home, monkeypatch, capsys):
    _only_this_env(monkeypatch)
    assert S.Settings(home).get()["home_control"] == {"enabled": False}
    code = cli.cmd_home(SimpleNamespace(state="on"), home, check=lambda creds: None)
    assert code == 1 and "HASS_TOKEN" in capsys.readouterr().out
    assert S.Settings(home).get()["home_control"]["enabled"] is False
    assert not (home / "config.yaml").exists()


def test_enable_with_credentials_checks_the_connection(home, monkeypatch, ha, capsys):
    _only_this_env(monkeypatch)
    before = (home / ".env").read_text()
    (home / ".env").write_text(before + f"HASS_URL={ha.url}\nHASS_TOKEN={TOKEN}\n")
    assert HC.credentials(home) == (ha.url, TOKEN)
    ha.status = 401
    assert cli.cmd_home(SimpleNamespace(state="on"), home) == 1
    assert "rejected" in capsys.readouterr().out
    ha.status = 200
    assert cli.cmd_home(SimpleNamespace(state="on"), home) == 0
    out = capsys.readouterr().out
    assert "is on" in out and TOKEN not in out
    assert S.Settings(home).get()["home_control"]["enabled"] is True
    assert cli.cmd_home(SimpleNamespace(state="off"), home) == 0
    assert S.Settings(home).get()["home_control"]["enabled"] is False
    assert not (home / "config.yaml").exists()


def test_setting_is_validated():
    with pytest.raises(S.SettingsError):
        S.validate({"home_control": {"enabled": "yes"}})


# -- voice wiring -------------------------------------------------------------------------------

def _call(server, service):
    status, _ = http(server.base_url, "POST", "/voice/sessions", {"sdp": SDP}, server.token,
                     {"Idempotency-Key": "req_home_1"})
    assert status == 201
    return service.workers[-1]


def _spoken(worker):
    return [c for k, _, c in worker.sent if k == "session.commentary.append"]


def _tasks(server):
    return http(server.base_url, "GET", "/voice/work/latest", token=server.token)[1]["tasks"]


def test_voice_light_command_is_answered_without_a_hermes_task(server, service, hermes, ha):
    service.home_control._creds = lambda: (ha.url, TOKEN)
    service.settings.patch({"home_control": {"enabled": True}})
    worker = _call(server, service)
    started = time.monotonic()
    worker.delegate("call_home_1", "Turn off the kitchen lights")
    wait_for(lambda: _spoken(worker))
    assert time.monotonic() - started < 1.5
    assert _spoken(worker) == ["Kitchen lights off."]
    assert ha.calls and ha.calls[0][1] == "turn_off"
    time.sleep(0.2)
    assert _tasks(server) == [] and hermes.calls == []


def test_voice_other_requests_still_go_to_hermes(server, service, hermes, ha):
    service.home_control._creds = lambda: (ha.url, TOKEN)
    service.settings.patch({"home_control": {"enabled": True}})
    worker = _call(server, service)
    worker.delegate("call_home_2", "Unlock the front door")
    wait_for(lambda: [t for t in _tasks(server) if t.get("run_id")])
    assert ha.calls == []


def test_voice_home_control_off_by_default_goes_to_hermes(server, service, hermes, ha):
    service.home_control._creds = lambda: (ha.url, TOKEN)
    worker = _call(server, service)
    worker.delegate("call_home_3", "Turn off the kitchen lights")
    wait_for(lambda: [t for t in _tasks(server) if t.get("run_id")])
    assert ha.reads == 0 and ha.calls == []


def test_voice_failure_is_spoken_briefly(server, service, hermes):
    fake = FakeHA(status=401)
    try:
        service.home_control._creds = lambda: (fake.url, TOKEN)
        service.settings.patch({"home_control": {"enabled": True}})
        worker = _call(server, service)
        worker.delegate("call_home_4", "Turn off the porch light")
        wait_for(lambda: _spoken(worker))
    finally:
        fake.close()
    assert "access token" in _spoken(worker)[0] and "hand it to" in _spoken(worker)[0]
    assert _tasks(server) == []


def test_status_reports_the_switch(server):
    status = http(server.base_url, "GET", "/voice/status", token=server.token)[1]
    assert status["home_control_enabled"] is False
