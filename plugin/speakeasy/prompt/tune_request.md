Tune my **voice brief** for Speakeasy from my recent calls.

Speakeasy's realtime voice model answers when I talk. At the start of each call it knows only this brief plus Speakeasy's fixed product rules; it cannot see your memory, tools or files. Below are the current brief and my recent calls: what I said, what the voice said, and how each task ended.

Find the moments where a better brief would have helped:
- the voice said it couldn't do something, didn't have access, or didn't know something, when you can or do
- it didn't know a person, place, project, device or preference that you know
- I had to repeat myself, correct it, or push back
- it answered in a way I clearly didn't like (too long, wrong tone, asked what it could have known)

For each, propose the smallest brief edit that would have prevented it. You may check your memory and my profile to confirm a fact (for example, whether you really can do the thing), but take no actions and don't message anyone.

Rules:
- At most 8 edits. Prefer changing or removing a stale or wrong line over adding a new one. Skip anything that happened once and doesn't matter.
- Every edit must cite the moment: a short quote and the day.
- Edits are to the brief only. Write lines the way the brief is written (first person as the assistant, short plain sentences).
- Never include secrets or sensitive data: no passwords, keys, tokens, account or card numbers, addresses, phone numbers, email addresses, or other people's private details.
- Some problems aren't about what the voice knows: slow answers, work landing in the wrong place, mishearing, the same thing done twice, a task that failed. Don't try to fix those in the brief; list them under product_issues.

Reply with strict JSON only, no prose:
{"summary": "one or two plain sentences on what you found",
 "edits": [{"kind": "add" | "change" | "remove",
            "section": "User" | "Assistant persona" | "Capability map" | "Answer preferences" | "Current context" (for add),
            "old": "the exact existing brief line (for change or remove)",
            "new": "the new line (for add or change)",
            "why": "what went wrong on the call, in plain words",
            "evidence": "short quote and day"}],
 "product_issues": [{"what": "the problem, in plain words", "evidence": "short quote and day"}]}

## Current brief

{brief}

## Recent calls

{calls}
