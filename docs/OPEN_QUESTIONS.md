# Open questions

Decisions made where the spec was silent or a choice would change the API
contract. Each lists the option picked (the simplest) so work could continue.

1. **Pause/resume.** Resume is `POST /voice/sessions` with `resume_from`; there is no separate
   `/resume` route.

2. **Approval choices.** Only `once` and `deny` are accepted from the app (no `always`/`session`). Broader approvals stay a Hermes-side decision.

3. **Voice-initiated email revise.** A spoken "revise it to ..." is routed as a follow-up to the
   task, so it reaches the same Hermes session and produces a new `email-draft`. The older draft of
   that task or session is marked `superseded` when the new one arrives, not at the moment the
   user speaks. The card's Revise button marks the old draft `revising` immediately.

4. **Email send result.** After Approve, the draft becomes `sent` unless the session's reply says
   it could not send (a small phrase check) or the run did not complete, in which case `failed`.
   Hermes has no structured "email sent" event today; a structured signal would be better.

5. **Draft limits.** At most 3 drafts per answer, 50 recipients per field, subject 300 chars,
   body 20,000 chars, plain text only. Attachments are not supported in v1.

6. **New threads per channel.** `delivery.channels[].new_thread` runs the task in a new thread
   through Speakeasy-owned routes on Hermes' webhook platform (needs `threads_supported`). This is
   covered by tests against a fake gateway; it has not yet been exercised against every platform
   Hermes can open threads on.

7. **Thread continuity source.** Candidates are live, non-internal Hermes sessions from `state.db`
   active in the last 21 days, matched by a deterministic word-overlap on session title and chat
   name (plus a continuation cue). No model call. False positives are guarded by requiring one
   clear best match; ambiguity starts a new task.

8. **Tailscale.** The architecture draft mentions `hermes voice setup --tailscale`. Not built: the
   server binds loopback only, and the user can front it with `tailscale serve` themselves and pass
   `--server <https url>` to `setup`/`pair`.

9. **Brief rewrite diff.** The server does not compute a diff before replacing an edited brief;
   `POST /voice/brief/rewrite` leaves an edited brief alone only for automatic refreshes. A manual
   rewrite from the app replaces it, and the app is expected to show the diff (old text is
   available from `GET /voice/brief` first).

10. **API server enablement.** Current Hermes enables the API server whenever a usable
    `API_SERVER_KEY` is set (there is no separate enable flag), so `hermes voice setup` only adds
    `API_SERVER_KEY` and `API_SERVER_HOST=127.0.0.1` to the profile `.env` when missing. If a
    future Hermes needs an explicit flag, setup must add it.
