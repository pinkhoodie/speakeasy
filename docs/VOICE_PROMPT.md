# How the voice prompt is built

The voice model (GPT-Live) is a separate model from the user's Hermes agent. It cannot read Hermes'
memory, skills or tools. Everything it knows at the start of a call comes from its instructions. So
the useful question is: **what does the voice model need to know to be a good front door to this
user's Hermes, and who writes it?**

Answer: behavior ships with the product; personal context comes from the user's own Hermes.
The tuned voice behavior (truthfulness, scoping, privacy, parallel tasks, delegation, results,
recap, resume, notices) ships in `prompt/rules.md` and `prompt/builder.py`, templated with
`{assistant_name}`, `{user_name}` and `{machine_description}` from settings, and works with no brief.
The user's Hermes writes the brief, which only adds personal context. Speakeasy never ships a persona.

## Three layers

1. **Product rules** (in this repo, `plugin/speakeasy/prompt/rules.md`, same for everyone)
   - How to hand work to Hermes: anything that needs facts, memory, tools or an action goes to the
     backend; never claim an action happened without a result.
   - Scope a vague request with at most one round of questions, then start.
   - Several requests can run at once; follow-ups attach to the right task.
   - Spoken style: short, plain sentences; no lists, markdown or URLs read aloud.
   - Never ask for or speak credentials. Interrupting speech does not cancel work.
   - Truthfulness: speak as the assistant in the first person; the work is done by the user's own
     Hermes on their own machine; never invent people or teams.
   - Email drafts: an email is never sent until the user presses Approve on its card; a spoken
     "send it" does not approve; changes go to the task as a follow-up.
   - The task prompt (`build_task_prompt`) tells Hermes to return email drafts as a fenced
     `email-draft` JSON block instead of sending (see API.md → Email drafts).
   - Screen: the voice never sees the screen. It speaks in first person ("I'll take your screen
     along with that"), never describes or claims to have seen what's on screen before a result,
     always hands off requests to look at, share or stop sharing the screen, and never says sharing
     started or stopped until a note says so. Notes tell it what went with a request (never names
     or contents) and, aloud, why a capture didn't.

2. **Voice brief** (written by the user's Hermes, stored at `<HERMES_HOME>/speakeasy/voice-brief.md`)
   - Who the user is: name, how to address them, time zone, languages.
   - Who the assistant is: its name and personality, taken from the agent's own persona.
   - **What it can do, in plain words**: a capability map built from the agent's real tools, skills,
     connected platforms and integrations ("I can check your calendar, send Telegram messages, control
     the living-room lights, search your notes"). This is the most important part: it tells the voice
     model when to hand off instead of guessing, and it stops it from offering things the agent can't do.
   - How the user likes answers: length, tone, units, things that annoy them.
   - Current context worth knowing: active projects or recurring topics, only at the level needed to
     understand references ("the Lisbon trip", "the Speakeasy repo").
   - Never: secrets, account numbers, addresses, health or financial detail, other people's private
     information. The voice provider is a third party; the brief is written with that in mind.
   - Capped at about 1,500 words.

3. **Per-call context** (assembled by the server at call start, never stored)
   - Local date and time.
   - "Earlier work": tasks that settled since the last call, as background. Finished work is not announced (it already reached the app and chat); an approval still waiting is mentioned once, after the user speaks.
   - Recent voice turns from this device's voice session, when the user has that setting on.
   - Resume: the conversation so far, when resuming a paused call.

## How the brief is written

- `hermes voice setup` (and the app's first-run screen) starts one Hermes run with the prompt in
  `plugin/speakeasy/prompt/brief_request.md`. The agent writes the brief from its own memory, user
  profile, persona and tool/skill list, following the section list above. The run goes through the
  user's normal Hermes, so it uses their model, memory and permissions.
- The server validates the result (size cap, required sections, a simple secret scan) and saves it.
- The brief is shown in the Mac app's Settings → Voice brief, where the user can read it, edit it, or
  press **Rewrite**. Edits are kept; the diff before replacing an edited brief is shown by the app
  (the server returns the new text; it does not compute diffs).
- Refresh: the server rewrites the brief in the background at most once a day, and only when the
  inputs changed (a hash of the memory, user profile, persona and enabled tools/skills). Never at call
  start, so calls never wait on it. Auto-refresh can be turned off; it is off automatically once the
  user has edited the brief by hand.
- If there is no brief yet (setup not finished, or the run failed), calls still work with the product
  rules alone and the assistant name from settings.

## Budget

GPT-Live instructions stay under ~12,000 tokens: product rules ~1,000, brief ≤ ~2,000, per-call
context the rest, trimmed oldest-first.

## Settings

- `assistant_name` (default "Hermes"), `user_name` (optional), `machine_description` (default "this Mac")
- `brief.auto_refresh` (default on)
- `brief.include_recent_voice` (default on)
- `instructions_extra` (short free text the user adds, e.g. "always answer in Spanish")
