# Speakeasy

Talk to your Hermes agent by voice from your Mac.

Speakeasy is a Hermes plugin plus a Mac app. The plugin runs inside your Hermes gateway; the app is a
floating voice panel you open with a hotkey. Voice uses your ChatGPT sign-in (Codex) by default, and
anything that needs real work goes to your own Hermes, with its tools, memory, skills and approvals.

Status: early, private. Mac only.

## Install (planned)

    hermes plugins install <repo-url>
    hermes plugins enable speakeasy
    hermes voice setup
    hermes gateway restart

Then open the Speakeasy app; setup pairs it automatically.

Other commands: `hermes voice pair [--send <target>]`, `hermes voice devices`,
`hermes voice revoke <id>`, `hermes voice config get|set <key> [value]`, `hermes voice status`.

See `docs/ARCHITECTURE.md`, `docs/VOICE_PROMPT.md`, `docs/API.md` and `docs/OPEN_QUESTIONS.md`.

## Tests

No network or paid calls: a fake Hermes API server, a fake `codex app-server` and a fake live
worker stand in for the real services.

    cd plugin && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests -q
    cd ~/.hermes/hermes-agent && venv/bin/python <repo>/plugin/tests/e2e_local.py

## License

MIT
