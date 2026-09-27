# Speakeasy

Talk to your Hermes agent by voice from your Mac.

Speakeasy is a Hermes plugin plus a Mac app. The plugin runs inside your Hermes gateway; the app is a
floating voice panel you open with a hotkey. The conversation runs on **OpenAI's GPT-Live-1** realtime
speech model, and anything that needs real work goes to your own Hermes, with its tools, memory,
skills and approvals.

**Download the Mac app:** [latest release](https://github.com/rungmc357/speakeasy/releases/latest)
(signed and notarized, macOS 14+) · Website: https://speakeasyvoice.ai

Status: early. Mac only.

## Voice model: GPT-Live-1, two ways to pay for it

| | How it signs in | Model | Who pays |
|---|---|---|---|
| **Codex OAuth** (default) | Your ChatGPT account, through `codex login` on the Hermes machine | `gpt-live-1-codex`, via `codex app-server` | Your ChatGPT plan, no API key |
| **OpenAI API** | An OpenAI API key (`hermes voice setup --api-key`) | `gpt-live-1`, via the Realtime API over WebRTC | Per-minute API usage on that key |

Either way the key or sign-in stays on the Hermes machine, never on the Mac. Switch any time in the
app (Settings › Voice) or with `hermes voice setup` / `hermes voice setup --api-key`. GPT-Live-1 only
talks and hands off; the actual work runs on whatever model your Hermes uses.

## Install

On the machine that runs Hermes:

    hermes plugins install rungmc357/speakeasy#plugin/speakeasy --enable
    hermes voice setup

`hermes voice setup` turns on Hermes' local API, signs you in to GPT-Live-1 with your ChatGPT account
through Codex OAuth (or asks for an OpenAI API key with `--api-key`), restarts Hermes so the voice server starts, waits until it
answers, and then pairs the Speakeasy Mac app with a one-time link and 6-digit code.

**Tailscale is picked up automatically.** When `tailscale` is installed and connected, setup
serves the voice server to your tailnet only (`tailscale serve`, never Funnel), prints a
`✓ Tailscale detected` box with the `https://<machine>.<tailnet>.ts.net:8795` address, and
remembers it so `hermes voice pair` reuses it. If Tailscale is installed but stopped, setup says
so loudly (run `tailscale up`) and stays local. If HTTPS certificates are off for your tailnet,
turn them on at https://login.tailscale.com/admin/dns (HTTPS Certificates) and run setup again.
`--tailscale` insists on it, `--no-tailscale` skips it, `--server <url>` sets the address by hand.

When the Mac is another computer, setup doesn't open the link on the Hermes machine: it offers to
send it to one of your connected Hermes chats (or `--send telegram`), and always prints the link
and code.

The app then asks for the microphone, what to call each other, where finished work should go
(it suggests the home channel of a connected chat such as Telegram or Discord), and whether to
continue existing conversations. In Settings › Delivery you can add more channels, each with a
short topic, so a new task goes where it belongs ("put this in #work" works too), optionally
in a new thread per task. **Suggest channels** asks your own Hermes to propose them.

Routing (follow-up or new task, splitting "do X and Y", picking a channel) uses one quick call to
your Hermes auxiliary model `speakeasy_router`; pick its model with `hermes model` → auxiliary
tasks. If it's slow or fails, simple built-in rules take over.

## Tests

No network or paid calls: a fake Hermes API server, a fake `codex app-server` and a fake live
worker stand in for the real services.

    cd plugin && ~/.hermes/hermes-agent/venv/bin/python -m pytest tests -q
    cd ~/.hermes/hermes-agent && venv/bin/python <repo>/plugin/tests/e2e_local.py

## License

MIT
