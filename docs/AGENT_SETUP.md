# Speakeasy setup, for your Hermes agent

You are a Hermes agent setting up Speakeasy for your user. Speakeasy is a Mac app that lets them
talk to you by voice with GPT-Live-1. You run everything on the machine where Hermes runs; the
user's only jobs are to restart Hermes when you ask, and to open one link on their Mac.

Do not assume you can control the user's Mac. Hermes often runs on another computer (a Mac mini,
a server). Never try to install or open anything on their laptop. You send them a link; they
click it.

## 1. Install the plugin

    hermes plugins install rungmc357/speakeasy#plugin/speakeasy --enable

If it's already installed, update it instead: `hermes plugins update speakeasy`.

## 2. Run setup and ask for the link in this chat

Run setup with `--send` set to the chat you are talking in right now, so the pairing link comes
back here. Use the platform name for the user's home chat (`telegram`, `discord`, `slack`, ...),
or `platform:chat_id` for a specific chat.

    hermes voice setup --send <this chat> --yes

- Voice uses **GPT-Live-1 through Codex OAuth**: the user's ChatGPT sign-in, no API key. If
  Codex is already signed in on this machine, nothing else is needed.
- If the user prefers the **OpenAI API** (their own key, billed per minute), add `--api-key`.
  Setup then asks for the key on a hidden prompt; if you can't answer hidden prompts, tell the
  user to put `OPENAI_API_KEY` in their Hermes `.env` themselves. Never ask them to paste a key
  in chat.
- Setup publishes the voice server on the user's Tailscale network when Tailscale is connected,
  so their Mac can reach it from anywhere on their tailnet. It never uses Tailscale Funnel.

## 3. Read the result and tell the user the one thing to do

Setup never restarts Hermes. Its exit code tells you where things stand:

- **0: done.** The pairing link was sent to this chat. Tell the user: open it on the Mac they'll
  talk from. If they don't have the app yet, the link offers the download first, then connects.
- **2: Hermes needs a restart.** Tell the user plainly that Speakeasy is installed and Hermes
  needs a restart to turn it on, and that they should restart it the way they normally do. Do not
  restart it yourself. Once Hermes is back, the pairing link arrives in this chat by itself.
- **1: something needs the user.** Setup prints what, in plain words (usually signing in to
  ChatGPT with `codex login`, or Tailscale HTTPS certificates being off). Pass that on in one or
  two sentences. When it's fixed, run setup again.

Do not paste pairing codes or links from setup's output into other chats. Each link works once and
expires after 30 minutes. For a new one: `hermes voice pair --send <this chat>`.

## What the user sees

They open the link on their Mac. If Speakeasy is installed, it opens and connects. If not, the
page offers "Download for Mac"; after they install it, the same link connects it. The app then
asks for microphone access, lets them pick names and where finished work goes, and starts a short
first call that shows them around.

More: https://speakeasyvoice.ai · Source: https://github.com/rungmc357/speakeasy
