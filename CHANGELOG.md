# What's new in Speakeasy

Speakeasy has two parts that update separately: the **Mac app** (updates through Check for Updates) and the **Hermes plugin** (updates itself in the background; no restart needed). Each entry says which one it came with.

## 2026-09-29 (later) — Mac app 0.2.12, plugin 0.2.26–0.2.28

**Tune your voice from your own calls**
- Settings › Voice brief › Tune from my calls: your Hermes reads the last week of calls and suggests specific edits to what the voice knows about you, each with the moment that prompted it. You tick the ones you want; nothing changes otherwise. Problems a brief can't fix are listed separately.
- Also from the terminal: `hermes voice tune start`.

**Smoother calls**
- Tasks that ran in their own chat thread no longer get stuck on "running" after you hang up or Hermes restarts. The finished answer is picked up from the thread, even days later.
- "What's the status?" is answered right away from what the task has done so far, instead of waiting behind it.
- "No, Hermes does" or "yeah, go" goes to the task you were just talking about, not a brand-new one.
- Half-words and repeats no longer start extra tasks. "Make 'em dimmer" right after a lights command is instant.
- The voice no longer says it can't do something or doesn't have access; it starts the work and lets the result say what happened.
- Settings › About links to this list.

## September 29, 2026

**Talk to your house** — Mac app 0.2.11, plugin 0.2.22–0.2.23
- If your Hermes runs Home Assistant, say "kitchen lights to 30 percent" or "den to 72 and office lights off" and it's done in about a second.
- When it isn't sure which one you mean ("set the thermostat to 68"), it asks once, then remembers your answer for the rest of the call.
- Setup offers it when Home Assistant is found. Settings › Home turns it on or off and picks which devices it can touch. Locks, garage doors and alarms always go through the full assistant.

**Calls feel more natural** — plugin 0.2.24–0.2.26
- "How's it going?" gets an answer right away from what the task is actually doing, instead of waiting behind it.
- Saying "no, that's wrong" or "yeah, do it" continues the thing you're talking about instead of starting something new.
- Half-heard scraps no longer start tasks, and saying the same thing twice doesn't start it twice.
- It no longer announces which thread or channel your work went to.
- Calling back later doesn't open with a list of everything that finished while you were away. Ask and it'll tell you.
- "Make them dimmer" right after a lights command is instant too.

## September 28, 2026

**Updates in place** — Mac app 0.2.10
- Check for Updates now installs the new version for you. You approve each one.

**Pictures and "show me"** — Mac app 0.2.7, plugin 0.2.9
- Pictures a task finds or makes pop up in the call panel. Arrow keys page through them.
- Ask "what does it look like?" and the picture opens on screen.

**Clearer while you wait** — Mac app 0.2.8, plugin 0.2.9–0.2.21
- Progress updates say what the task is doing and found, not "still on it", and they come less often.
- If a request doesn't go through, it says so instead of waiting forever.
- Work picks up in the right earlier conversation by what was said there.
- Choose which of your Hermes models sorts your requests (Settings › Task routing).

## September 27, 2026

**Email drafts and task names** — Mac app 0.2.5–0.2.6, plugin 0.2.5–0.2.8
- Email drafts show as a card. Nothing is sent until you press Send.
- Tasks get real names and a short status while they work.
- Pick your assistant's voice, with samples to listen to.

**Speakeasy 0.2.0**
- Talk to your Hermes agent by voice from your Mac: it hands off work, keeps talking while it runs, and tells you when it's done.
- One-message setup through your own agent, a first-call tour, editable shortcuts, and delivery to your Discord or Telegram channels.
