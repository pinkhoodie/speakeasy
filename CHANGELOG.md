# What's new in Speakeasy

Speakeasy has two parts that update separately: the **Mac app** (updates through Check for Updates) and the **Hermes plugin** (updates itself in the background; no restart needed). Each entry says which one it came with.

## Plugin 0.2.35 — Cards behind every quick answer

- **Prices answer in under a second, with a chart.** "Where's Apple at", "how's ETH today", "what's the S&P doing" read live stock and crypto prices (no web search) and come with a price card: ticker, today's change and the day's chart.
- **Every quick answer now carries a card** for the apps to draw: the weather with hourly and 7-day forecasts, a game with both teams' logos and the score, a league's slate of games, a clock showing there and here, the math worked out.
- **Full Hermes tasks can show cards too.** When an answer is better seen than heard (a place, a route, a flight, a package, a day's schedule, a comparison, a recipe, a draft), the task adds a card alongside its answer. 41 card types in all. Apps that don't know a card type yet simply skip it.

## Plugin 0.2.34

- **Home city is exact.** Neighborhood names the map service doesn't know (or shares with another town) no longer land somewhere else: `hermes voice fast home "Neighborhood, City (lat, lon)"` pins it.

## Plugin 0.2.33

- **Instant clock, date and math.** "What time is it in Tokyo", "what's the date", "what's 18 percent of 240" are answered on your machine with no search: under a tenth of a second, always right.
- **Weather in about three seconds,** from live forecast data rather than a web search: now, today, tomorrow, the week, and the next 24 hours. Set a home city for questions that don't name one: `hermes voice fast home <city>`.
- **"Who's playing Monday night?" and other league questions** ("any hockey games tonight", "who won the baseball games last night") now come from the live scoreboard in about two seconds.
- **Quick answers start looking things up while the request is still being sorted,** saving about half a second each.

## Plugin 0.2.32

- **Quick answers in a couple of seconds.** Simple public questions ("how tall is…", "who owns…", "what time is sunset") are answered from one web search instead of a full Hermes task. Anything the search doesn't clearly answer still goes to Hermes, so it never guesses.
- **Sports scores, instantly.** "Did the Mets win?", "when do the Knicks play next?" come straight from live scoreboard data in about a second and a half: final score, who won, home or away, next game.
- **Optional Jev routing.** Connect TypeSafe's Jev through Venice, OpenRouter or TypeSafe and every request is sorted (home, quick answer, Hermes task) in about half a second, instead of the several seconds the routing model takes. Off by default; `hermes voice fast jev venice` turns it on. Without Jev, plainly worded questions still get the quick lane.
- `hermes voice fast` shows and changes all of this.

## 2026-10-01 — plugin 0.2.31

**What it says is what happens**
- When a task is running in its own chat thread, whatever you say about it on the call now goes into that thread, as if you'd typed it there: answering its question ("the 4:15 works"), adding something, or telling it to hold off. The voice only says it passed something on after it actually got there, and tells you plainly when it couldn't.
- The task card follows a thread task's progress ("found Thursday open, pulling times") instead of switching to "Status unconfirmed" after a minute and a half.
- "Show me the options when you have them" reaches the task as a request for a picture, instead of getting "there's no picture to show".
- A new subject that starts with "and also" becomes its own task instead of being added to whatever you asked just before. A single question, even a long one, stays one task.
- The first-call tour only plays on your very first call. A new Mac, a reinstall or an update no longer replays it.

**More visual**
- When an answer is something to look at or choose from, like open times, options, a place or an order summary, the task sends a screenshot with it and the picture opens in the app during the call.

**Home control**
- Short follow-ups right after a home command ("turn them back on") and short names for devices ("the pendants") stay on the instant path instead of going the slow way through Hermes.
- A request that was heard as two pieces ("Bedroom lamps at forty percent" … "purple") is handled as one sentence.

## 2026-09-30 — Mac app 0.2.13

**Choose where Speakeasy lives**
- A new setup screen lets you keep the Dock icon or hide it and use just the menu bar icon. Switch any time with Settings › General › Show in Dock.

## 2026-09-29 (later) — Mac app 0.2.12, plugin 0.2.26–0.2.30

**Tune your voice from your own calls**
- Settings › Voice brief › Tune from my calls: your Hermes reads the last week of calls and suggests specific edits to what the voice knows about you, each with the moment that prompted it. You tick the ones you want; nothing changes otherwise. Problems a brief can't fix are listed separately.
- Also from the terminal: `hermes voice tune start`.

**Smoother calls**
- Your voice channel now logs every voice task. When the answer lands in a thread, another channel or an older conversation, a one-line "Done: … →" link appears in the voice channel pointing to it. "Still working" and "Stopped" lines use the task's name instead of your exact words.
- The voice no longer reads terminal commands out loud. It tells you there's one to run, and the command shows up in chat as its own message, so a long-press copies just the command.
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
