# Ambient Listening Mode — Implementation Plan & Contract

Implements issue rungmc357/speakeasy#2. Behavior contract ported from Buddy
(/Users/nk/GitHub/Buddy), verified against Buddy's source.

Doctrine: **listen always (local, free), speak only when spoken to, billed realtime
only after the wake word.** Default OFF until the user enables it.

## Cross-stack contract (both sides build to THIS)

### Settings (`plugin/speakeasy/settings.py` DEFAULTS + validation)

```python
"listening.enabled": False,
"listening.wake_phrases": ["hey speakeasy"],
"listening.wake_variants": [],            # user-extensible misheard variants
"listening.debounce_seconds": 5.0,
"listening.end_of_utterance_silence": 1.0,
"listening.max_question_seconds": 15.0,
"listening.hot_context_words": 2000,
"listening.compress_interval_seconds": 60.0,
"listening.compress_target_words": 600,
"listening.summary_every": 3,
"listening.mode_b_max_tokens": 120,
```

All patches validated; unknown `listening.*` keys rejected like every other section.

### Endpoints (`plugin/speakeasy/server.py`, all bearer-auth)

- `GET  /voice/listening/state` → `{"enabled": bool, "memory_chars": int, "last_activity_at": ts|null}`
- `POST /voice/listening/transcript` `{"text": str, "ts": iso}` → dedup-vs-tail append → `{"stored": bool}` (false = duplicate)
- `GET  /voice/listening/memory` → `{"hot": [segments newest-last], "compressed": str, "summary": str|null, "title": str|null, "subtitle": str|null, "compression_count": int}`
- `POST /voice/listening/compress` → server-side compression cycle (processing model) → `{"compressed": str, "compression_count": int, "summary": str|null, "title": str|null, "subtitle": str|null}` (summary/title only when `compression_count % summary_every == 0`)
- `POST /voice/listening/wake` `{"mode": "question"|"insight", "question": str?, "context_tail": str}` →
  - Mode B (`insight`): cheap completion over ambient memory → `{"answer": str}` — no realtime session
  - Mode A (`question`): returns guidance for the mac to open an ephemeral seeded session: `{"open_session": true, "seed": {"ambient": {...block below...}}}`

### Session seeding (`POST /voice/sessions` optional body key)

```json
"ambient": {
  "mode": "question",
  "question": "what's on my plate today?",
  "hot_context": "…recent transcript…",
  "compressed": "…compressed memory…",
  "summary": "…last summary or null…"
}
```

`prompt/builder.build_live_instructions` injects this as an ambient block:
the session starts ALREADY knowing the room; never greets; answers the seeded
question first. Routing/dispatch/handoff semantics unchanged (router sees the
question as a normal interaction).

### Storage (`plugin/speakeasy/store.py`)

- Table `ambient_transcript(id INTEGER PK, ts TEXT, text TEXT)` — bounded (keep last ~2000 rows)
- Table `ambient_memory(key TEXT PK, value TEXT)` — keys: `compressed`, `summary`, `title`, `subtitle`, `compression_count`
- Meta keys: `ambient_last_activity`

### Mac side

- `mac/Sources/SpeakeasyCore/Ambient/` — pure, unit-tested logic:
  - `VADGate` (RMS over 20 ms frames @16 kHz → threshold 0.01, ≥3 consecutive frames = speech; mirrors Buddy VADService)
  - `WakeWordDetector` (lowercased substring match over phrases+variants, 5 s debounce, Mode A/B split on trailing words)
  - `QuestionAccumulator` (append across cycles until ≥1 silent cycle ≈ end_of_utterance_silence, hard cap max_question_seconds)
  - `TranscriptDeduper` (exact-suffix or ≥70% progressive-prefix overlap vs rolling tail)
- `mac/Sources/Speakeasy/Native/AmbientListeningEngine.swift` — AVAudioEngine tap (16 kHz mono), VADGate before STT, on-device SFSpeechRecognizer behind `AmbientTranscribing` protocol, 1.5 s gated cadence, 60 s ring buffer, bias-tail (~250 chars) continuity, posts to `/voice/listening/transcript`; SUSPENDS while a realtime call is open, resumes after.
- Mode A: filler ack (pre-warmed AVSpeechSynthesizer bank: "Mm.", "Hmm, let me think.", …) → NativeVoiceClient opens ephemeral seeded session → existing pause/idle machinery closes it.
- Mode B: `/voice/listening/wake` answer → AVSpeechSynthesizer; interruptible (existing stop paths).
- UX: Settings toggle (default OFF) + menu-bar Listen toggle + ambient hotkey; listening state shown as a calm indicator (not red); never greets; mute respected.

## Non-goals

Spontaneous speech; audio leaving the Mac before a wake word; raw-audio wake
detection; router/handoff changes.

## Verification gates

`swift test` · plugin `pytest` · offline `tests/e2e_local.py` · contract fixtures
regenerated if server payloads changed · `scripts/scan-secrets.sh`
