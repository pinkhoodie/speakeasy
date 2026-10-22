# Text replies in the expanded chat view (plan)

Status: building (2026-10-20). Source: `docs/brainstorms/2026-10-20-text-replies-in-chat-threads-requirements.md`,
final "Decision" section.

## Scope (from the decision)

- When a task finishes with a written answer, the **full text** shows as a reply bubble in the call panel's
  **expanded** transcript ("Read the full exchange"). The compact caption (one line per side) is unchanged.
- The voice flow is unchanged: the realtime voice model still gets `result["spoken"]` through
  `session.commentary.append` and speaks it. Text is additive, not a mode.
- No new setting, toggle, route, or chat-target delivery. No change to `Notices.answered`.

## What already exists (verified)

- `calls.py` result branch stores `split_result(output)` → `{"spoken", "full", ...}` with `store.set_result`,
  then speaks `result["spoken"]`.
- `store.work()` → `public_result(result)` already puts `result.full` on every task in the `tasks` SSE event /
  `/voice/work/latest` snapshot. The app decodes it as `WorkInfo.result.full` (`ServerModels.swift`).
  **So the plugin needs no code change**; a contract test pins it (full text reaches the app *and* the spoken
  line still goes to the voice).

## Units

### U1. Plugin contract test (no plugin code change)
- `plugin/tests/test_text_replies.py`: a completed task's `tasks[].result.full` carries the whole answer, and
  the worker's `session.commentary.append` still carries the spoken line (not the full text).

### U2. Core: text-reply turns in the call transcript (`SpeakeasyCore/VoiceState.swift`)
- `TranscriptTurn` gains `kind: .speech | .text` (default `.speech`, additive initializer) and an optional
  `taskID`. `cleanedText` leaves text replies as written (the bracket-tag cleaner would eat markdown link names).
- Reducer, `.tasks`: when a task the state had seen still running becomes `completed` with a written answer,
  append one `.text` assistant turn with `result.full`. Skip when: the call view is work-only; the task's answer is
  already counted as seen (home commands); the full text is the same as the spoken line (quick answers — the
  voice already says all of it); the task already got a bubble (`textRepliedTaskIDs`).
  Requiring the running→completed edge keeps earlier calls' carried tasks (drafts/reviews) out of this call.
- `.outputDelta` only extends a `.speech` assistant turn, so the voice's words never run into the text bubble.

### U3. Mac view: render it only in the expanded transcript (`Speakeasy/Native/VoicePanelView.swift`)
- `TranscriptBubble` draws `.text` turns as a wider, selectable reply card ("<Name> · written reply",
  inline markdown: bold, links). The collapsed caption reads `exchange`, which never includes text replies,
  so the compact panel is unaffected by construction.

### U4. Tests (`mac/Tests/SpeakeasyCoreTests/TextRepliesTests.swift`)
- running→completed appends one text turn with the full answer; repeated snapshots don't duplicate.
- voice deltas after the text reply start a new speech turn; `exchange` (compact caption) never holds the text.
- tasks first seen already completed, `seen` home commands, full == spoken, failed tasks, and work-only: no turn.

## Verification
- `~/.hermes/hermes-agent/venv/bin/python -m pytest tests -q` in `plugin/`.
- `swift build` + `swift test` in `mac/` (scratch path on the SSK drive), and `--panel-smoke` if it runs headless.

## Open questions (not blocking)
- Quick answers / short answers where `full` == `spoken` show no extra bubble (the voice transcript already has
  the words). Confirm that's wanted.
- iPhone/visionOS share `SpeakeasyCore`: they get the text turns in `transcript` but render them with their own
  views; no change made there.
