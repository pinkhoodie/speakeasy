---
title: "feat: Duck the Mac's volume while a voice call is speaking"
type: feat
status: active
date: 2026-10-20
---

# feat: Duck the Mac's volume while a voice call is speaking

## Summary

While either side of a voice call is talking, lower the Mac's system output volume to a set level, and
put the previous volume back after a few seconds of transcript silence or when the call closes. A first
cut (`plugin/speakeasy/duck.py`, two hooks in `plugin/speakeasy/calls.py`) already runs on a live install; this
plan hardens it so it is safe to ship upstream: bound to the service's Hermes home, configurable through
the validated settings file, cheap on the hot transcript path, and covered by tests that never touch the
real audio device.

## Problem Frame

Music or video playing on the Mac competes with the voice call. Ducking it while someone speaks keeps the
call intelligible without the user reaching for the volume keys.

The first cut has defects found while preparing it for review:

- It resolves its state and settings paths from `HERMES_HOME` / `~/.hermes`, not from the `VoiceService`
  home. The plugin test suite (which never sets `HERMES_HOME`) therefore read and wrote the real
  `~/.hermes/speakeasy/duck-state.json` and drove the real Mac volume; running the suite during a live
  call captured the ducked level as the "previous" volume and left the Mac stuck at 30%.
- `audio_duck` is not a known settings key, so `validate()` drops it: the first settings PATCH from the
  app or `hermes voice config set` silently erases the user's ducking preferences.
- Every transcript delta (many per second) re-reads `settings.json`, enqueues a duck job, and starts a new
  `threading.Timer` thread.
- Restoring always forces the saved volume back, even if the user changed the volume mid-duck.

## Requirements

- R1. Either side's transcript delta lowers the output volume to `audio_duck.level` (default 30) unless
  output is muted or ducking is off.
- R2. After `audio_duck.hold_s` (default 4 s) with no transcript, or when the last call that spoke closes,
  the previous volume comes back. Muting mid-duck does not count as a user change (the level comes back;
  unmuting stays with the user).
- R3. A crash or live reload while ducked restores the saved volume the next time the plugin starts.
- R4. If the user changes the volume while ducked, restore leaves their choice alone.
- R5. `audio_duck.enabled|level|hold_s` are validated settings (survive PATCH, settable via
  `hermes voice config set`), and `SPEAKEASY_VOICE_DUCK=0` is a kill switch.
- R6. All state is scoped to the service's Hermes home; nothing runs on non-macOS; the asyncio loop never
  blocks or spawns a thread per delta.
- R7. Tests cover the above without invoking `osascript`, and the suite as a whole cannot touch the real
  volume.

## Scope Boundaries

- No Mac/iPhone UI toggle (settings are reachable via `hermes voice config` and settings.json).
- No per-app ducking (macOS has no native API; would need third-party audio software). Ducking lowers the
  assistant's own voice too; the level default reflects that.
- No plugin version bump / CHANGELOG entry here: the release commit assigns the version (open PR #5 already
  claims 0.3.12).

## Key Technical Decisions

- **Ducker object owned by `VoiceService`, reached via `Runtime.duck`.** Mirrors how `home`, `call_log`
  and `notices` are wired; the home comes from the service, and tests/other `Runtime` users get a no-op
  when it is absent. Replaces the module-global singleton.
- **One worker thread with a deadline, not a Timer per delta.** `note()` only extends a deadline and
  enqueues a duck at the start of a cycle; the worker waits on its queue with a timeout and restores when
  the deadline passes. Settings are read once per cycle.
- **Injectable volume backend.** The ducker takes a small get/set backend; the default shells out to
  `osascript`, tests pass a fake. A suite-wide autouse fixture stubs the default backend, so no test can
  reach the real volume.
- **Restore checks the current level.** The state file records both the original volume and the level we
  set; restore only writes when the current volume is still within a step of what we set, and the state
  file is removed only after the volume is back (a failed set keeps it for the next restore or start).
- **Only the gateway's service drives the volume.** `hermes voice ...` CLI commands build a short-lived
  `VoiceService` (`start_threads=False`); it gets no backend so its startup restore cannot undo a duck
  the gateway is holding mid-call.
- **Per-call restore.** `note`/`restore` carry the interaction id; a call ending restores at once only
  when no other call spoke this cycle, otherwise the quiet spell decides.
- **Default on.** Matches the behaviour already running; called out for the maintainer in the PR.

## Implementation Units

### U1. Ducker hardening

**Goal:** Home-bound, deadline-driven ducker with injectable backend and user-change detection.
**Requirements:** R1–R4, R6.
**Files:** `plugin/speakeasy/duck.py`, `plugin/tests/test_duck.py`.
**Approach:** `Ducker(home, settings_fn, backend=None)`; `note(speaker)`, `restore()`, `close()`.
Startup restore of any leftover state. Non-darwin default backend is inert.
**Test scenarios:**
- Happy path: a delta ducks to the configured level and saves the prior volume; after hold expires the
  prior volume is restored and the state file is removed.
- Repeated deltas inside the hold window keep one cycle (one duck, no extra backend writes) and push the
  restore out.
- `restore()` restores immediately; a second `restore()` is a no-op.
- Muted output: no duck, no state file.
- Volume unreadable (backend returns None): no duck, no state file.
- Leftover state file at construction (crash/reload): volume restored, file removed.
- User changed volume while ducked: restore does not overwrite it; state file still removed.
- Previously-muted state restores mute.
- Disabled via settings and via `SPEAKEASY_VOICE_DUCK=0`: no backend calls.
- Settings parsing: out-of-range level/hold are clamped or rejected per settings validation.

### U2. Settings key

**Goal:** `audio_duck` becomes a validated settings group.
**Requirements:** R5.
**Files:** `plugin/speakeasy/settings.py`, `docs/API.md`, `plugin/tests/test_duck.py`.
**Test scenarios:** default values present; PATCH keeps `audio_duck` and another PATCH does not drop it;
invalid `enabled`/`level`/`hold_s` rejected with a named field.

### U3. Wiring

**Goal:** Service owns the ducker; calls use `rt.duck`; close restores.
**Requirements:** R1, R2, R6, R7.
**Files:** `plugin/speakeasy/calls.py`, `plugin/speakeasy/service.py`, `plugin/tests/conftest.py`,
`plugin/tests/test_duck.py`.
**Test scenarios:** integration — a `VoiceService` built with a fake backend, fed an input-transcript
delta through the worker's event handler, ducks; `session.closed` restores; `Runtime` without a ducker
handles transcript events without error.

## Risks

- Two Hermes profiles running Speakeasy on one Mac share one system volume but separate state files; a
  simultaneous duck in both could restore out of order. Rare; noted, not solved.
- A hard kill while ducked leaves the volume low until the plugin starts again (startup restore).
