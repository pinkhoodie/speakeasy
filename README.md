# Speakeasy

Talk to your Hermes agent by voice from your Mac.

Speakeasy is a Hermes plugin plus a Mac app. The plugin runs inside your Hermes gateway; the app is a
floating voice panel you open with a hotkey. Voice uses your ChatGPT sign-in (Codex) by default, and
anything that needs real work goes to your own Hermes, with its tools, memory, skills and approvals.

Status: early, private. Mac only.

## Install

On the machine that runs Hermes:

    hermes plugins install rungmc357/speakeasy#plugin/speakeasy --enable
    hermes voice setup

`hermes voice setup` turns on Hermes' local API, signs you in to ChatGPT through Codex (or asks
for an OpenAI API key with `--api-key`), restarts Hermes so the voice server starts, waits until it
answers, and then opens a one-time link that pairs the Speakeasy Mac app. Add `--tailscale` when
the Mac and the Hermes machine are different computers on the same tailnet.

The app then asks for the microphone, what to call each other, and where finished work should
go. It suggests the home channel of a connected chat such as Telegram or Discord.

## Tests

No network or paid calls: a fake Hermes API server, a fake `codex app-server` and a fake live
worker stand in for the real services.

    cd plugin && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests -q
    cd ~/.hermes/hermes-agent && venv/bin/python <repo>/plugin/tests/e2e_local.py

## License

MIT
