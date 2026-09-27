# Speakeasy Mac app — open questions

Decisions made so the port could ship; each took the simplest option. Revisit with the plugin owner.

## Server contract

1. **`/voice/destinations` shape.** The client accepts `{"destinations": [...]}`, a bare array, or bare strings,
   and always offers "None" first. Needs confirming against `docs/API.md` once the plugin port writes it.
2. **`/voice/onboarding` GET shape.** Read as `{"steps": {"<step>": bool}}`, `{"done": [ids]}` or `{"completed": true}`. The app
   uses it only to skip already-done steps; if the route is missing (404) onboarding runs every step.
3. **`threads_supported`** is read from `/voice/status`; missing = false (toggle hidden).
4. **`user_name`, `delivery.{target,new_thread_per_task}`, `continuity.enabled`** are sent through
   `PATCH /voice/settings` from Settings (only changed keys are sent). No delivery target is sent as `"none"`.
5. **Email drafts:** `POST /voice/drafts/{draft_id}` is assumed to return 200 with the updated draft or `{ok}`;
   the app refetches the task either way. 409 → refetch + "The draft changed — review it again".
6. **Voice picker list** is hard-coded per provider (the public Realtime voice names). A `voices` list from
   `/voice/status` or `/voice/settings` would be better.
7. **Pair response** is read as `{device_id, token}` from `POST /voice/pair`.

## App behavior

8. **Panel after End** auto-hides after 4 s unless hovered or an approval / pending email draft / waiting task
   needs attention. Rule lives in `SpeakeasyCore/PanelVisibility.swift` (tested). The 4 s constant is not a
   setting.
9. **Hotkey while a call is live** ends the call rather than toggling panel visibility.
   The close (x) button hides the panel during a call; the menu bar "Show panel" brings it back.
10. **Mute/Pause shortcuts** keep their defaults (⌃⌥M, ⌃⌥P) and are not in the Settings recorder yet;
    only the call hotkey is recordable.
11. **Launch at login** uses `SMAppService.mainApp`; it only works from a signed `.app` in a stable location —
    from `swift run` the toggle shows the error the system returns.
12. **Ad-hoc signing** means macOS asks for microphone access again after every rebuild. Set
    `SPEAKEASY_CODESIGN_IDENTITY` to a stable identity to avoid that. Notarization is out of scope.
13. **Bundle id** defaults to `co.speakeasy.mac`; override with `SPEAKEASY_BUNDLE_ID` at package time.
14. **"Parallel tasks"** in Settings › Behavior is an explanatory note only (the server always allows it).
15. **Continuity / "while you were away"** notifications fall back to a menu-bar badge when macOS notifications
    are unavailable (e.g. unsigned `swift run`).

## Not in v1

- iPhone and Watch apps.
- Quiet-reply button (the logic is in the core, not exposed in the panel).

The device token lives in the Keychain and the server URL in UserDefaults; only pairing sets them.
