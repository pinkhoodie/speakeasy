# Speakeasy

Talk to your Hermes agent by voice from your Mac.

Speakeasy is a Hermes plugin plus a Mac app. The plugin runs inside your Hermes gateway; the app is a
floating voice panel you open with a hotkey. The conversation runs on **OpenAI's GPT-Live-1** realtime
speech model, and anything that needs real work goes to your own Hermes, with its tools, memory,
skills and approvals.

**Download the Mac app:** [Speakeasy.dmg](https://github.com/rungmc357/speakeasy/releases/latest/download/Speakeasy.dmg) (all versions: [releases](https://github.com/rungmc357/speakeasy/releases))
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

**Let your agent do it.** Send this to your Hermes agent in any chat you already use with it:

> Set up Speakeasy for me, so I can talk to you by voice from my Mac. Follow the instructions at https://speakeasyvoice.ai/setup.md

The agent installs the plugin on the machine Hermes runs on, turns on voice, and sends you one link.
Two things are yours to do:

1. **Restart Hermes when it asks.** Setup never restarts anything itself. Once Hermes is back, the
   link arrives in that chat by itself.
2. **Open the link on the Mac you'll talk from.** It opens Speakeasy and connects it. If the app
   isn't installed yet, the page offers the download first, and the same link connects it after.

Hermes can run on the Mac you talk from or on another computer; the agent checks which, then
opens the link for you or sends it to your chat. [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md) is the
exact guide the agent follows.

**By hand**, on the machine that runs Hermes:

    hermes plugins install rungmc357/speakeasy#plugin/speakeasy --enable
    hermes voice setup --send telegram      # the chat that should get the link: discord, slack…

Setup turns on Hermes' local API, signs you in to GPT-Live-1 with your ChatGPT account through
Codex OAuth (or asks for an OpenAI API key with `--api-key`), and sends the pairing link. If
Hermes has to restart first, setup says so, and the link is sent once you've restarted it.
When Hermes and the app are on the same Mac, setup opens the link directly.

**Tailscale is picked up automatically.** When `tailscale` is installed and connected, setup
serves the voice server to your tailnet only (`tailscale serve`, never Funnel), prints a
`✓ Tailscale detected` box with the `https://<machine>.<tailnet>.ts.net:8795` address, and
remembers it so `hermes voice pair` reuses it. If Tailscale is installed but stopped, setup says
so loudly (run `tailscale up`) and stays local. If HTTPS certificates are off for your tailnet,
turn them on at https://login.tailscale.com/admin/dns (HTTPS Certificates) and run setup again.
`--tailscale` insists on it, `--no-tailscale` skips it, `--server <url>` sets the address by hand.

Links sent to a chat go to `https://speakeasyvoice.ai/pair#…`; the server address and one-time code
sit after the `#`, which browsers never send to the website. Each link works once and expires after
30 minutes; `hermes voice pair --send <chat>` makes a new one.

The app then asks for the microphone, what to call each other, where finished work should go
(it suggests the home channel of a connected chat such as Telegram or Discord), and whether to
continue existing conversations. In Settings › Delivery you can add more channels, each with a
short topic, so a new task goes where it belongs ("put this in #work" works too), optionally
in a new thread per task. **Suggest channels** asks your own Hermes to propose them.

Routing (follow-up or new task, splitting "do X and Y", picking a channel) uses one quick call to
your Hermes auxiliary model `speakeasy_router`; pick its model with `hermes model` → auxiliary
tasks. If it's slow or fails, simple built-in rules take over.

## Contributing

Building from source, running the tests and releasing: see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT

Made by [Georgio Constantinou](https://georgio.co) · [@rungmc357](https://github.com/rungmc357)
