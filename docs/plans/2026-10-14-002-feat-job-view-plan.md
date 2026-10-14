# Job view: orb, job board, decision briefs, visual cards (plan + contract)

Status: building (2026-10-14). Mac 0.3.0 + plugin 0.3.0. Mac only; iOS/visionOS stay on today's contract (every change below is additive).

## What the user gets
1. **Orb mode** (replaces "slim"): clicking the orb folds the whole panel into a small orb. The call keeps running.
   Orb states: calm (idle), breathing (user talking), soft pulse (a task is running), amber + count badge
   (something needs you: question, approval, email draft, design review), red (a task failed, not dismissed).
   Clicking the orb opens the panel on the **board**; Escape folds it back.
2. **Board**: one tile per **job**, not per task. Sections: Needs you (decisions first, with Approve / See options
   inline when allowed), Running (one-line status + step progress), Done today (compact chips).
   Keyboard: up/down move, Return opens, 1-3 picks on a focused decision, Escape collapses.
3. **Job view** (click a tile): "Talking to: <job>" chip, the job's tasks, the decision brief if one is waiting,
   the report board (ResultShape: checks, shipped, open, sections), the result's visual cards, live steps
   (events) with Tell it something / Stop, and "Next: <next job that needs you>". Back/Escape returns to the board.
   Opening a job calls `setFocus(taskID: <newest task of the job>)`; closing it clears focus.
4. **Decision brief**: question with plain context, why it's recommended, and options carrying detail, tags and
   reversibility. Row shows "RECOMMENDED" + the recommended option in words, buttons **Approve** and **See options**.
   Approve only when the plugin marks the decision `approvable`.
5. **Visual card kinds** the agent can emit: lanes, before_after, flow, devices (plus existing chart, stats,
   progress, steps, list, comparison).
6. **Routing**: the job on screen is a strong prior, not a hard override.

## Contract (plugin → app). All additive; old apps ignore new fields.

### Task JSON (`tasks[].info` / work): new fields
```json
"job": {"id": "20261004_150034_4059657b", "name": "Night shift"}
```
- `id`: the Hermes session id the run lives in (runs.session_id, else the `continued` placement's session_id,
  else the idem_key). Tasks with the same id are one job, across calls and days.
- `name`: short job name (2-4 words) from the work itself, never a Discord thread title. Stored per job id;
  set once from the first task title in that job (title model may refine it later). Never empty.

### Question view (`kind: "question"`): new optional fields
```json
{"kind": "question", "question": "When should the router stop sending work to a Studio?",
 "context": "Your Mac mini splits local-model work between zeus and apollo...",
 "why": "Keeps the swap protection and halves a both-down window.",
 "options": [
   {"text": "1 error, 30s bench", "detail": "Keeps the swap protection.", "tags": [{"text": "Low risk", "tone": "good"}],
    "risk": "low", "reversible": true},
   {"text": "3 errors, 30s bench", "detail": "Swap errors may return.", "risk": "medium", "reversible": true},
   {"text": "Leave it"}],
 "recommended": 0,
 "affects": "1 file on the mini · all local-model jobs",
 "approvable": true}
```
- `options` stays a list; each entry is either a string (old) or an object {text, detail, tags[{text, tone}],
  risk (low|medium|high), reversible (bool), image (existing)}. tone ∈ good|warn|bad|neutral.
- `approvable` is computed by the plugin, never trusted from the model: true only when there is a recommended
  option whose risk is "low" and reversible is true, and the question/option text doesn't involve money, sending
  as the user, publishing, or deleting (keyword guard). Otherwise false/absent.
- Answering is unchanged (`POST .../answer` with the option text).

### New view kinds
- `lanes` — what ran when / where it broke.
  `{title, start_label, end_label, ticks[S, 6], lanes[{label, segments[{from N 0..1, to N 0..1, tone, label}] (8)}] (6), note}`
- `before_after` — `{title, before{label, text, image}, after{label, text, image}}` (image = https URL or
  MEDIA-style card image number, same rules as existing image fields).
- `flow` — where something goes / pipeline. `{title, nodes[{label, detail, tone}] (5)}`
- `devices` — fleet grid. `{title, items[{name, value, detail, tone, history[S, 6]}] (8)}`
tone ∈ good|warn|bad|info|neutral. Bars use only numbers from the data; the app never invents values.

### Prompt
VIEWS_RULE gains the four kinds; QUESTION_RULE asks for context/why/option objects for beefy decisions and says
"build the card from facts you checked; prefer one visual (lanes/before_after/flow/devices/chart) that explains
the decision".

### Routing (plugin)
Focus becomes a prior: when a task is in focus and the request isn't clearly new, the router model is told
"the user is looking at <job>; send it there unless clearly unrelated". Home commands stay instant.
Every routed request records where it went (existing events).

## Bugs folded in
- Result `full` text sometimes stored twice (seen in 3 runs on 10-12). Find and fix.
- Spoken summary splices sentences out of context (a sentence about something never introduced). Fix the picker.

## Not in this build
Card clusters around the orb, drag-to-pin, iOS/visionOS UI, merging jobs across threads.
