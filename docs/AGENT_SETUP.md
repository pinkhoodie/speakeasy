# Speakeasy setup, for your Hermes agent

You are a Hermes agent setting up Speakeasy for your user. Speakeasy is a Mac app that lets them
talk to you by voice with GPT-Live-1. You install it on the machine where Hermes runs. The user's
only jobs are to restart Hermes when you ask, and to open one link on the Mac they'll talk from.

That Mac may be the machine Hermes runs on, or a different one (Hermes often lives on a Mac mini
or a server while the user talks from a laptop). Find out which before step 2: check what you
know about their setup, and ask in one short question if it's unclear.

## 1. Install the plugin

    hermes plugins install rungmc357/speakeasy#plugin/speakeasy --enable

If it's already installed, update it instead: `hermes plugins update speakeasy`. Then check
`hermes plugins show speakeasy`: if it says `Status: not enabled`, run
`hermes plugins enable speakeasy --no-allow-tool-override`. Updating files alone does not
necessarily reactivate a disabled plugin. **Tell the user to restart Hermes themselves after
an update**, even when the CLI reports that the update succeeded; do not restart it for them.

The paired Mac app checks its latest GitHub release and the published plugin manifest separately
about once a week while it is running. It compares the manifest version against the *running*
Hermes plugin, so a Mac-only release does not trigger a false plugin alert. A newer plugin gets a
separate menu and Settings notice, and a one-time macOS notification (if allowed). It does not
auto-install the plugin or restart Hermes. If the app cannot reach Hermes, it cannot check the
running plugin version; help the user restore the connection first.

## 2. A different Mac? Make sure Tailscale connects them

Skip this step when the user talks from the machine Hermes runs on.

Otherwise their Mac reaches Hermes over Tailscale, privately, with nothing opened to the
internet. Check this machine with `tailscale status`:

- **Connected:** good. Setup publishes the voice server to their tailnet only.
- **Installed but stopped:** run `tailscale up` if you can; if it needs a browser sign-in, give
  the user the sign-in link it prints.
- **Not installed:** tell the user Speakeasy needs Tailscale when Hermes is on another computer,
  and install it if you can (`brew install --cask tailscale` on a Mac, or the command at
  https://tailscale.com/download for Linux). Signing in needs them once.

Their Mac needs Tailscale too, signed in with the **same account**: https://tailscale.com/download.
Say so in the same message, since you can't check their Mac from here (unless you can operate it).

If setup reports that HTTPS certificates are off for the tailnet, the user turns them on once at
https://login.tailscale.com/admin/dns (HTTPS Certificates), then you run setup again.

## 3. Run setup

**Same Mac as Hermes:** setup opens the pairing link on this Mac itself: Speakeasy if it's
installed, otherwise the download page, which connects the app once it's in.

    hermes voice setup --here --yes

**A different Mac:** have the link sent to the chat you're talking in right now. Use the platform
name for the user's home chat (`telegram`, `discord`, `slack`, ...), or `platform:chat_id` for a
specific chat.

    hermes voice setup --send <this chat> --yes

If you can also operate the user's other Mac, you may install or open the app there, but the
link in chat is enough on its own.

- Voice uses **GPT-Live-1 through Codex OAuth**: the user's ChatGPT sign-in, no API key. If
  Codex is already signed in on this machine, nothing else is needed.
- If the user prefers the **OpenAI API** (their own key, billed per minute), add `--api-key`.
  Setup then asks for the key on a hidden prompt; if you can't answer hidden prompts, tell the
  user to put `OPENAI_API_KEY` in their Hermes `.env` themselves. Never ask them to paste a key
  in chat.
- Setup publishes the voice server on the user's Tailscale network when Tailscale is connected
  (never Tailscale Funnel, so nothing is exposed to the internet).

## 4. Read the result and tell the user the one thing to do

Setup never restarts Hermes. Its exit code tells you where things stand:

- **0: done.** The pairing link was opened on this Mac, or sent to this chat. Tell the user which,
  and to open it on the Mac they'll talk from if it was sent. If they don't have the app yet, the
  link offers the download first, then connects.
- **2: Hermes needs a restart.** Tell the user plainly that Speakeasy is installed and Hermes
  needs a restart to turn it on, and that they should restart it the way they normally do. Do not
  restart it yourself. Once Hermes is back, the pairing link arrives in this chat by itself (when you used `--send`).
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
