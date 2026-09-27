"""`hermes voice setup` end to end with every side effect faked: order of steps, restart, voice
sign-in, Tailscale, and that the pairing link only opens once the voice server answers."""
from __future__ import annotations

import os
import stat
from argparse import Namespace
from types import SimpleNamespace

import pytest

from speakeasy import cli, setup_flow as F
from speakeasy import settings as S


class Fake:
    """Records commands; answers like a machine with Codex signed in and Hermes running."""

    def __init__(self, *, codex_signed_in=True, codex_installed=True, restart_ok=True, comes_up=True,
                 brew=True, answers=None, secret="", interactive=True, tailscale_dns="box.tail123.ts.net"):
        self.cmds: list[list[str]] = []
        self.lines: list[str] = []
        self.codex_signed_in, self.codex_installed = codex_signed_in, codex_installed
        self.restart_ok, self.comes_up, self.brew = restart_ok, comes_up, brew
        self.answers = list(answers or [])
        self.secret_value, self.interactive, self.dns = secret, interactive, tailscale_dns
        self.restarted = False
        self.health_checks = 0
        self.now = 0.0

    def run(self, cmd, **kw):
        cmd = [str(c) for c in cmd]
        self.cmds.append(cmd)
        joined = " ".join(cmd)
        if joined.endswith("login status"):
            ok = self.codex_signed_in
            return SimpleNamespace(returncode=0 if ok else 1, stdout="Logged in using ChatGPT" if ok else "Not logged in", stderr="")
        if cmd[-1] == "login":
            self.codex_signed_in = True
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if "gateway" in cmd and ("restart" in cmd or "start" in cmd):
            self.restarted = self.restart_ok
            return SimpleNamespace(returncode=0 if self.restart_ok else 1, stdout="", stderr="")
        if cmd[:2] == ["brew", "install"]:
            self.codex_installed = True
            return SimpleNamespace(returncode=0)
        if "status" in cmd and "--json" in cmd:
            return SimpleNamespace(returncode=0, stdout='{"Self": {"DNSName": "%s."}}' % self.dns, stderr="")
        if "serve" in cmd:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def ask(self, prompt):
        self.lines.append("? " + prompt)
        return self.answers.pop(0) if self.answers else ""

    def health(self, url):
        self.health_checks += 1
        return self.restarted and self.comes_up

    def which(self, name):
        return {"brew": "/opt/homebrew/bin/brew" if self.brew else None,
                "tailscale": "/usr/local/bin/tailscale"}.get(name)

    def env(self):
        return F.Env(run=self.run, ask=self.ask, secret=lambda p: self.secret_value, out=self.lines.append,
                     interactive=self.interactive, health=self.health, sleep=self.sleep, which=self.which,
                     clock=lambda: self.now)

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.delenv("_HERMES_GATEWAY", raising=False)
    monkeypatch.setattr(cli, "_home", lambda: tmp_path)
    monkeypatch.setattr(F, "gateway_running", lambda h: True)
    monkeypatch.setattr(F, "find_codex", lambda configured="": _codex(tmp_path, fake_holder))
    return tmp_path


fake_holder: dict = {}


def _codex(tmp_path, holder):
    fake = holder.get("fake")
    return (tmp_path / "codex") if fake is None or fake.codex_installed else None


def args(**kw):
    base = dict(voice_command="setup", server="", tailscale=False, api_key=False, send="", yes=False,
                no_restart=False, no_open=True)
    base.update(kw)
    return Namespace(**base)


def run_setup(fake, **kw):
    fake_holder["fake"] = fake
    opened = []
    orig = cli._open
    cli._open = lambda link: opened.append(link) or True
    try:
        rc = cli.cmd_setup(args(**kw), cli._home(), env=fake.env())
    finally:
        cli._open = orig
    return rc, opened


def test_happy_path_restarts_waits_then_opens_link(home):
    fake = Fake()
    rc, opened = run_setup(fake, no_open=False)
    assert rc == 0
    assert any("gateway" in c and "restart" in c for c in fake.cmds)
    assert fake.restarted and fake.health_checks >= 1
    assert len(opened) == 1 and opened[0].startswith("speakeasy://pair?server=http%3A%2F%2F127.0.0.1")
    text = "\n".join(fake.lines)
    assert "✓ Voice: your ChatGPT account" in text and "✓ Voice server is running" in text
    assert "Opened Speakeasy" in text


def test_link_is_not_opened_when_the_voice_server_never_comes_up(home):
    fake = Fake(comes_up=False)
    rc, opened = run_setup(fake, no_open=False)
    assert rc == 1 and opened == []
    assert "didn't come up" in "\n".join(fake.lines)


def test_declining_restart_explains_and_does_not_open(home):
    fake = Fake(answers=["n"])
    rc, opened = run_setup(fake, no_open=False)
    assert rc == 1 and opened == [] and not fake.restarted
    assert not any("restart" in c for c in fake.cmds)


def test_inside_a_hermes_chat_never_restarts(home, monkeypatch):
    monkeypatch.setenv("_HERMES_GATEWAY", "1")
    fake = Fake()
    rc, _ = run_setup(fake, yes=True)
    assert rc == 1 and not any("restart" in c for c in fake.cmds)


def test_signs_in_to_codex_when_needed(home):
    fake = Fake(codex_signed_in=False)
    rc, _ = run_setup(fake, yes=True)
    assert rc == 0 and any(c[-1] == "login" for c in fake.cmds)
    assert S.Settings(home).get()["voice"]["provider"] == "codex"


def test_installs_codex_with_homebrew(home):
    fake = Fake(codex_installed=False)
    rc, _ = run_setup(fake, yes=True)
    assert rc == 0 and ["brew", "install", "codex"] in fake.cmds


def test_api_key_path_stores_key_privately_and_never_prints_it(home):
    key = "sk-" + "a" * 40
    fake = Fake(secret=key)
    rc, _ = run_setup(fake, api_key=True, yes=True)
    assert rc == 0
    assert S.read_env_file(home / ".env")[S.OPENAI_KEY_NAME] == key
    assert stat.S_IMODE(os.stat(home / ".env").st_mode) == 0o600
    assert S.Settings(home).get()["voice"]["provider"] == "openai"
    assert key not in "\n".join(fake.lines)
    assert not any("login" in c for c in fake.cmds)


def test_rejects_something_that_is_not_an_api_key(home):
    fake = Fake(secret="hunter2")
    rc, _ = run_setup(fake, api_key=True, yes=True)
    assert rc == 1 and S.OPENAI_KEY_NAME not in S.read_env_file(home / ".env")


def test_tailscale_serves_tailnet_only_and_links_to_it(home):
    fake = Fake()
    rc, _ = run_setup(fake, tailscale=True, yes=True)
    assert rc == 0
    serve = next(c for c in fake.cmds if "serve" in c)
    assert "funnel" not in " ".join(serve) and "--bg" in serve
    text = "\n".join(fake.lines)
    assert "https%3A%2F%2Fbox.tail123.ts.net%3A8795" in text


def test_existing_key_and_config_are_kept(home):
    (home / ".env").write_text("API_SERVER_KEY=keep-this-existing-value\nOTHER=1\n")
    fake = Fake()
    rc, _ = run_setup(fake, yes=True)
    assert rc == 0
    env = (home / ".env").read_text()
    assert env.count("API_SERVER_KEY=") == 1 and "keep-this-existing-value" in env and "OTHER=1" in env
    assert "keep-this-existing-value" not in "\n".join(fake.lines)


def test_rerunning_setup_is_harmless(home):
    fake = Fake()
    assert run_setup(fake, yes=True)[0] == 0
    before = (home / "config.yaml").read_text()
    fake2 = Fake()
    fake2.restarted = True  # already running with the voice server up: no restart needed
    assert run_setup(fake2, yes=True)[0] == 0
    assert (home / "config.yaml").read_text() == before
    assert not any("restart" in c for c in fake2.cmds)


def test_setup_turns_voice_on_for_the_real_gateway_loader(home):
    """What setup writes must make Hermes' own plugin-platform check say 'configured'."""
    import yaml
    import speakeasy
    run_setup(Fake(), yes=True)
    cfg = yaml.safe_load((home / "config.yaml").read_text())
    block = cfg["gateway"]["platforms"]["voice"]
    probe = SimpleNamespace(enabled=True, extra=dict(block["extra"]))
    assert speakeasy.is_connected(probe)
    assert not speakeasy.is_connected(SimpleNamespace(enabled=True, extra={}))
