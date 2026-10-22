# Ideation: text replies in chat threads

Date: 2026-10-20 · Status: ideation only (no design/schema decisions)

## The ask

Nir (paraphrased): "respond with text in chat threads. Maybe it still uses the voice model, but
only transcribes in both directions or something. I'm not exactly sure."

Interpretation: the user keeps talking to Speakeasy; their speech is still transcribed and routed
as today, but the assistant's **reply** arrives as **text in a chat thread** (the app's
conversation view and/or a Hermes chat target) instead of — or alongside — spoken TTS.

## What exists today (verified in code)

- **Split answer.** A finished run produces `result["full"]` (text for reading) and
  `result["spoken"]` (short spoken variant) via `plugin/speakeasy/text.py` (`split_result`,
  `spoken_from` at `text.py:103`; `delivery_text` at `text.py:571`).
- **Text delivery already exists for tasks.** On `run.completed`,
  `Notices.answered` (`plugin/speakeasy/calls.py:439`) posts the **full** answer via
  `hermes send` (`plugin/speakeasy/delivery.py:39` HermesSendNotifier) to the task's routed
  channel `backend.deliver_to`, and `Notices.pointer` (`calls.py:431`) posts a one-line pointer to
  the home target so the voice channel stays "a complete log of every voice task".
  But **the voice also speaks** the short variant right after
  (`calls.py:2365+` result branch → `session.commentary.append` with `result["spoken"]`).
  So today chat gets text *in addition to* speech, never instead of it, and only when a
  `delivery.target`/routed channel is set.
- **Threads as conversations.** `plugin/speakeasy/threads.py` opens a real Hermes thread per task
  (webhook routes, `source_new_thread`); Hermes replies land there as its own messages and the
  user can follow up in-thread (`Notices.commands` comment, `calls.py:454`).
- **"Know but never say" channel.** `SidebandWorker.quiet` /
  `session.thinking.append` (`calls.py:768-807`) feeds the voice model context it must not read
  aloud; already used when packing result notes (`calls.py:2395+`, `HOLD_QUIET_NOTES`).
- **Suppressible speech.** `speech.progress` setting gates spoken progress
  (`settings.py:39`, `maybe_speak_progress` `calls.py:1031`); heads-up sentences
  (`HEADS_UP_RE`, `text.py:59`) exist to protect critical info when speech is cut.
- **Routing.** `plugin/speakeasy/channels.py` picks the destination for a new task (named /
  topic / default, `Channel.new_thread`); `plugin/speakeasy/router.py` splits follow-up vs new
  task; `channels.allows_conversation` (`channels.py:125`) decides if voice work may continue an
  existing chat.
- **App side.** `mac/Sources/SpeakeasyCore/ServerModels.swift` already models `WorkResult`,
  `InteractionSnapshot`, event streams; `mac/Sources/SpeakeasyCore/RoomTranscript.swift` and
  `Jobs/JobViews.swift` render transcripts/results. Settings flow through `POST /voice/settings`
  (`plugin/speakeasy/server.py`).
- **Session config.** `openai_live.session_payload` (`plugin/speakeasy/openai_live.py:45`) sets
  `audio.output.voice`; whether the gpt-live/Codex session supports text-only output modality is
  **unverified** — treat as unknown.

## Approaches

### A. Per-call output-mode flag ("text mode" for this conversation)

**Mechanism.** Add a per-session flag (settings default + a field on `POST /voice/sessions` /
`POST /voice/settings`, e.g. `reply_mode: voice | text`). While a call runs in text mode, the
result branch in `_handle_hermes_event` skips the spoken `commentary.append` of
`result["spoken"]` and sends the answer as a quiet note instead; the full text reaches the thread
through the **already-existing** `Notices.answered` path (with a fallback target when
`delivery.target` is `none`). Voice phrase ("answer in text from now") and/or an app toggle set
the flag; the router model can classify the phrase.
**Touches:** `calls.py` (result branch, quiet notes, say_where/progress gating),
`settings.py` (DEFAULTS + validate), `server.py` (route passthrough),
`prompt/builder.py` (result_notes wording), small Swift toggle
(`ServerSettings.swift`, panel UI).
**Pros:** smallest honest version of "transcribes both ways"; reuses proven plumbing
(quiet notes, notices, split_result); reversible mid-call; scope is explicit.
**Cons:** two sources of truth for "where did my answer go" (app + chat target) need one
consistent rule; voice model may still *want* to reply aloud to quiet notes — needs prompt-side
instruction ("user is reading, don't speak answers").
**Effort:** M. **Ambiguous:** toggle surface (app only vs spoken phrase); what a text-mode
session does with quick answers and home-control replies (currently spoken inline).

### B. Hybrid delivery — text-first, TTS as opt-in replay

**Mechanism.** Always deliver the full text to the thread (existing `Notices.answered`) and show
it as a reply bubble in the app interaction; the voice speaks **only** heads-up sentences
(`HEADS_UP_RE` filter over `result["full"]`, machinery already in `spoken_from`). Anything longer
is announced in one line ("full answer is in the thread") and the user can tap to have it spoken
now (a `session.commentary.append` on demand — note: audio is not stored, so "replay" means
re-speaking, not playing back a recording).
**Touches:** `calls.py` result branch + a small "speak this" action route in `server.py`;
`text.py` (a `spoken_first_line`/heads-up-only picker); mac `WorkResult` rendering
(`ResultCards.swift`, `JobViews.swift`).
**Pros:** best of both — text is authoritative, speech stays for warnings and asks; no new
session/provider behavior needed; degrades gracefully (no target set → behaves like today).
**Cons:** changes the *default* feel of every call (opinionated); "tap to speak" is a new app
interaction; picking which sentences stay spoken is heuristic and will misfire sometimes.
**Effort:** M–L. **Ambiguous:** is silent-by-default acceptable for casual Q&A
("what's the weather")? Cards/views (`show_me`, `calls.py:1551`) already go to the screen — do
they get a spoken one-liner too?

### C. Routing-aware text-first (destination decides)

**Mechanism.** Keep voice as the default, but when a task is dispatched to a chat target or
thread (`channels.Choice`, `backend.deliver_to` set, or `threads.py` thread opened), the reply is
**text-first there and not spoken** — the voice gets only the existing pointer line. In-call
answers with no destination keep speaking. In effect: "if you sent it somewhere readable, you'll
read it there."
**Touches:** mostly the same result branch in `calls.py` + a rule tying `deliver_to` to
speech suppression; `channels.py`/`router.py` unchanged; docs (`docs/ARCHITECTURE.md`).
**Pros:** coherent mental model ("speech = ephemeral, threads = durable"); almost no new state;
consistent with today's pointer behavior.
**Cons:** doesn't cover Nir's core case — a plain question with no channel named still gets
spoken; user can't opt into text *without* also choosing a destination; risk of silence
surprises when routing silently picks a channel (`channels.resolve`).
**Effort:** S–M. **Ambiguous:** does "dispatched to a thread" include continuity follow-ups into
existing chats (`continue_in_thread`, `calls.py:2011`)?

### D. Dual-delivery always

**Mechanism.** Make every completed answer post full text to a thread (default `delivery.target`
non-none, app thread always) *and* keep full spoken behavior unchanged. This is mostly
configuration + making the app's interaction view read like a chat log
(`InteractionSnapshot` events already carry results).
**Touches:** `settings.py` defaults + onboarding (`setup_flow.py`), mac transcript/interaction UI.
**Pros:** trivial server-side; text log exists retroactively for every task; no routing risk.
**Cons:** doesn't reduce speech at all — probably not what Nir asked; double-presenting
everything is noisy; chat targets get spammed for small talk (`_talk_not_work`,
`calls.py:1153`).
**Effort:** S. **Ambiguous:** whether the voice should then *abbreviate* what it says since text
is available (if yes, this converges toward B anyway).

## Recommendation

**B (hybrid, text-first with heads-up-only speech), reached by building A first.**
A is the enabling mechanism (an explicit, reversible mode) and B is its first shipped
configuration. Rationale: every load-bearing piece already exists and is tested —
`split_result`/`spoken_from` for the text/spoken split, `quiet`/`session.thinking.append` for
"know but never say", `Notices.answered`/`delivery_text` for thread delivery, `speech.progress`
as precedent for a speech gate. The genuinely new work is one result-branch rule in
`calls.py`, one settings field, and app UI for reading replies + a toggle. C is a good
*default policy inside* B later ("replies to threaded tasks are already text") but is too
narrow alone; D doesn't change what Nir complained about.

## Open questions for Nir

1. **Scope of the toggle:** per-call (my assumption), per-thread, or a global setting? Set from
   the app UI, a spoken phrase, or both?
2. **Which thread receives text:** the app conversation only, or also a Hermes chat target
   (Telegram/Discord)? What should happen when `delivery.target` is `none` — app-only text, or
   refuse text mode?
3. **Silence policy:** in text mode, is *everything* silent, or do heads-ups still get spoken
   (failures, approvals, "waiting on you")? Approvals already have a chat fallback
   (`Notices.needs_you`) — reuse it?
4. **Mid-task status:** progress/milestone lines — posted to the thread as text lines, shown
   only as app events, or suppressed?

## Smallest version that proves the bet

A per-call "text replies" toggle (app panel button → `POST /voice/settings` session override).
While on: completed answers skip the spoken commentary, go out as a quiet note to the voice, and
post via the existing `Notices.answered` to the current delivery target (or app-only when none);
failures/approvals keep speaking (reuse `HEADS_UP_RE` + `Notices.needs_you`). Progress stays
governed by the existing `speech.progress` setting. Success signal: a full workday where long
answers are read in the thread, voice is only interrupted for warnings — and follow-ups spoken
aloud still route correctly (no router changes needed).

## Decision (Man, 2026-10-20 voice)

- Text replies show ONLY in the expanded chat view of the app (not the compact panel).
- Keep the same voice flow: the Codex/ChatGPT realtime voice model still runs and still speaks
  even when a text reply is written — text is additive, not a replacement mode.
