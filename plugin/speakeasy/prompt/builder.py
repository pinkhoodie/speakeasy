"""Every piece of instruction text Speakeasy gives the voice model and the user's Hermes.

Product behavior lives here and in ``rules.md``, templated with ``{assistant_name}``,
``{user_name}`` and ``{machine_description}``. It works with no voice brief: the brief only adds
personal context on top.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..text import clean_transcript, notice_text, safe_user_text

PROMPT_DIR = Path(__file__).parent
# ~12,000 tokens at ~4 chars/token.
MAX_INSTRUCTION_CHARS = 48_000
MAX_BRIEF_CHARS = 10_000
RESULT_BACKGROUND_CHARS = 1900  # live-call appends over 2,000 characters are dropped

# GPT-Live `session.input` limits: 128 messages and 8,192 tokens. Stay well inside.
RESUME_MAX_MESSAGES = 120
RESUME_MAX_CHARS = 24_000
RESUME_MESSAGE_CHARS = 2_000


@dataclass(frozen=True)
class Names:
    assistant_name: str = "Hermes"
    user_name: str = ""
    machine_description: str = "this Mac"

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> "Names":
        return cls(settings.get("assistant_name") or "Hermes", settings.get("user_name") or "",
                   settings.get("machine_description") or "this Mac")

    @property
    def user(self) -> str:
        return self.user_name or "the user"

    @property
    def user_cap(self) -> str:
        return self.user_name or "The user"

    @property
    def possessive(self) -> str:
        return f"{self.user_name}'s" if self.user_name else "the user's"

    def vars(self, **extra: str) -> dict[str, str]:
        return {"assistant_name": self.assistant_name, "user_name": self.user, "user_name_cap": self.user_cap,
                "user_possessive": self.possessive, "machine_description": self.machine_description, **extra}


def render(template: str, names: Names, **extra: str) -> str:
    """Fill only known {placeholders}; any other braces are left as they are."""
    values = names.vars(**extra)
    return re.sub(r"\{([a-z_]+)\}", lambda m: values.get(m.group(1), m.group(0)), template)


def delivery_clause(delivery_label: str, channels: list[dict[str, Any]] | None = None) -> str:
    """Where finished work goes, so the voice can answer "where will that go?"."""
    clause = f" and, when a task finishes after the call, in {delivery_label}" if delivery_label else ""
    if not channels:
        return clause
    listed = "; ".join(f"{c['label']} ({c.get('topic') or 'anything named for it'}"
                       + (", each task in its own new thread" if c.get("new_thread") else "") + ")"
                       for c in channels)
    fallback = delivery_label or "the app only"
    return (clause + ". New tasks can also go to these chats: " + listed + ". A task goes where "
            + "{user_name} names (\"start this in <channel>\"), else to the channel whose topic fits, else to "
            + fallback + "; follow-ups stay where their task runs. When a task starts, say in one short line where "
            "it went")


def rules_text(names: Names, delivery_label: str = "", channels: list[dict[str, Any]] | None = None) -> str:
    template = (PROMPT_DIR / "rules.md").read_text(encoding="utf-8").replace(
        "{delivery_clause}", delivery_clause(delivery_label, channels))
    return render(template, names).strip()


def truthfulness(names: Names) -> str:
    return render(
        "Speak in the first person as {assistant_name}: the backend work is your own work, not someone you hand off to. "
        "Say things like \"let me check\", \"I'm looking into it\", or \"on it\"; never \"I'll check with Hermes\", "
        "\"I'll ask the backend\", or \"I'll let you know what they say\". "
        "Stay truthful about how it works: Hermes runs any background workers on {machine_description}. "
        "Never invent people, teams, or colleagues, and explain the setup honestly if {user_name} asks.", names)


# -- the voice session ------------------------------------------------------------------

def away_block(away: list[dict[str, Any]], names: Names) -> str:
    lines = []
    for item in away[:3]:
        request = item.get("request") or "an earlier request"
        status = item.get("status")
        if status == "waiting_for_approval":
            outcome = f"needs {names.possessive} approval"
        elif status == "completed":
            outcome = "finished" + (f": {item['spoken']}" if item.get("spoken") else "")
        else:
            outcome = f"stopped ({status})"
        lines.append(f"- {request}: {outcome}")
    if not lines:
        return ""
    return ("# While you were away\nSince your last call:\n" + "\n".join(lines)
            + f"\nOpen the call by telling {names.user} this in one or two sentences, then listen.")


def resume_block(tasks: list[dict[str, Any]], names: Names) -> str:
    """Instructions for a call resumed after Pause: continue, don't restart."""
    lines = [
        "# Resumed call",
        f"{names.user_cap} paused this call and has just resumed it. The conversation so far is in your history. "
        "Do not greet them or introduce yourself again; pick up exactly where you left off. "
        "If you were in the middle of an answer that still matters, finish it briefly; otherwise wait for them.",
    ]
    open_tasks = []
    for task in tasks[-6:]:
        request_event = next((e for e in task.get("events") or [] if e.get("kind") == "request"), None)
        request = notice_text((request_event or {}).get("text"), 140) or "an earlier request"
        status = task.get("status")
        if status in {"completed", "failed", "cancelled", "interrupted", "ambiguous"}:
            spoken = notice_text((task.get("result") or {}).get("spoken"), 300)
            outcome = "finished" + (f": {spoken}" if spoken else "") if status == "completed" else f"stopped ({status})"
            open_tasks.append(f"- {request}: {outcome}")
        elif status == "waiting_for_approval":
            open_tasks.append(f"- {request}: waiting for {names.possessive} approval in the panel")
        else:
            short = notice_text(task.get("short_status"), 80)
            open_tasks.append(f"- {request}: still working" + (f" ({short})" if short else ""))
    if open_tasks:
        lines.append("Tasks from this call (results that arrived while paused were not spoken yet):\n" + "\n".join(open_tasks))
    return "\n".join(lines)


def resume_input(history: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Newest-first trimmed transcript as GPT-Live startup history (oldest first on the wire)."""
    picked: list[dict[str, Any]] = []
    total = 0
    for turn in reversed(history):
        text = clean_transcript(turn.get("text") or "").strip()
        role = turn.get("role")
        if not text or role not in {"user", "assistant"}:
            continue
        text = text[-RESUME_MESSAGE_CHARS:]
        if len(picked) >= RESUME_MAX_MESSAGES or total + len(text) > RESUME_MAX_CHARS:
            break
        total += len(text)
        part = "input_text" if role == "user" else "output_text"
        picked.append({"type": "message", "role": role, "content": [{"type": part, "text": text}]})
    picked.reverse()
    return picked


def build_live_instructions(names: Names, *, brief: str = "", away: list[dict[str, Any]] | None = None,
                            recent_voice: str = "", resume: str = "", extra: str = "", now: str = "",
                            delivery_label: str = "", channels: list[dict[str, Any]] | None = None,
                            max_chars: int = MAX_INSTRUCTION_CHARS) -> str:
    """Product rules + optional voice brief + per-call context, under the budget.

    Rules are never trimmed. The brief is capped. Optional per-call blocks are dropped
    lowest-priority first (recent voice, then away) when over budget; resume is always kept.
    """
    head = rules_text(names, delivery_label, channels)
    if extra.strip():
        head += "\n\n# Extra instructions from " + names.user + "\n" + extra.strip()[:1000]
    if brief.strip():
        head += "\n\n# About " + names.user + " and " + names.assistant_name + " (voice brief)\n" + brief.strip()[:MAX_BRIEF_CHARS]
    if now:
        head += f"\n\n# Now\nLocal date and time: {now}."
    tail = [resume.strip()] if resume.strip() else []
    optional = []  # lowest priority last so it is trimmed first
    block = away_block(away or [], names)
    if block:
        optional.append(block)
    if recent_voice.strip():
        optional.append("# Recent voice conversation (for continuity; do not read aloud)\n" + recent_voice.strip())
    fixed = len(head) + sum(len(p) + 2 for p in tail)
    while optional and fixed + sum(len(p) + 2 for p in optional) > max_chars:
        optional.pop()
    return "\n\n".join([head, *optional, *tail])


def format_recent(messages: list[dict[str, Any]], names: Names, limit: int = 20, per_message: int = 400) -> str:
    lines = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        text = " ".join(content.split())
        if len(text) > per_message:
            text = text[:per_message - 1] + "…"
        speaker = (names.user_name or "User") if role == "user" else names.assistant_name
        lines.append(f"{speaker}: {text}")
    return "\n".join(lines[-limit:])


# -- the Hermes task --------------------------------------------------------------------

PRODUCT_CARDS_RULE = (
    "When recommending products, include a JSON array in a fenced block labeled `product-cards` before DONE/SPOKEN. "
    "Each object has name, url, and only verified fields: price, store, rating, image_url (direct HTTPS image), "
    "and specs (up to three short strings). Find a real product image URL with tools; never invent prices, ratings, images, "
    "or destinations. Omit a field you cannot verify. Number products in your prose to match the array order. "
)

EMAIL_DRAFT_RULE = (
    "When this task produces an email to send on {user_possessive} behalf, do NOT send it. Write the draft and end "
    "your answer with a fenced block labeled `email-draft` containing one JSON object: {\"from\": \"<sender address>\", "
    "\"to\": [\"...\"], \"cc\": [], \"bcc\": [], \"subject\": \"...\", \"body\": \"<plain text>\", "
    "\"reply_to_message_id\": \"<optional>\", \"account\": \"<optional mail account>\"}, then the DONE and SPOKEN lines, "
    "and stop. {user_name_cap} reviews it on a card in the app and approves, denies or asks for changes there; "
    "you will be told in this session. Never send an email without that approval message. "
)


def status_rule(names: Names) -> str:
    return render(
        "{user_name_cap} watches a one-line live status while you work. Before your first tool call, write interim commentary "
        "exactly as two lines: STATUS: <specific present-tense action of 2-6 words, e.g. Checking the weather forecast> "
        "then DETAIL: <brief user-facing detail>. Write a new pair only when the step meaningfully changes. "
        "Never include reasoning, credentials, or raw tool arguments. "
        "End your final answer with two last lines: DONE: <past-tense label of 1-5 words, e.g. Weather checked> "
        "then SPOKEN: <one or two plain sentences to say aloud>; "
        "the full answer above them is shown on screen and may include links and detail.", names)


def build_task_prompt(names: Names, revision: int, context: str, focus: str | None = None,
                      delivery_label: str = "") -> str:
    """Prompt for a voice task running in its own Hermes session, beside other tasks.

    The session is private to this task (and its follow-ups), so it names the job and carries the
    call transcript for context."""
    where = (f"your final answer is still shown in the Speakeasy app and posted to {delivery_label}"
             if delivery_label else "your final answer is still shown in the Speakeasy app")
    return (
        render("You are {assistant_name}, handling one task {user_name} gave you by voice from Speakeasy on their Mac. "
               "This session belongs to this task alone; other tasks from the same call run in their own sessions at "
               "the same time. Use your normal tools, memory, skills and session-history lookup when the user refers "
               "to another project or conversation. Do the work yourself in this session, start to finish, even if it "
               "takes a while: {user_name} can hang up and ", names)
        + where + ", so write the full answer for reading and keep the SPOKEN line for speech. "
        "Do not create Kanban tasks, cron jobs, or background workers for a voice request, and do not hand it to "
        "another board or session. "
        + truthfulness(names) + " "
        "Do not auto-approve consequential actions. Return concise verified facts and status suitable for speech. "
        + PRODUCT_CARDS_RULE + render(EMAIL_DRAFT_RULE, names)
        + (render("Your task: ", names) + f"{focus} " + render(
            "Other parts of what {user_name} said run as separate tasks; do not do them, and do not "
            "redo or cancel another task's work. ", names) if focus else
           "Your task is the most recent user request in the transcript below; earlier requests are context and "
           "have their own tasks, so do not redo or cancel their work. ")
        + status_rule(names) + "\n\n"
        f"Recent timestamped voice transcript (revision {revision}):\n{context}"
    )


def steer_text(names: Names, request: str) -> str:
    return f"{names.user_cap} just added to this task by voice: {request} Fold it into the work you are doing; do not start over."


def follow_up_focus(request: str, earlier: str, result: str | None, status: str | None) -> str:
    return (f"{request} (This follows up an earlier task: \"{earlier}\""
            + (f", which finished with: {result}" if result else f", status {status}") + ".)")


def continuation_message(names: Names, request: str, summary: str) -> str:
    """The user turn written into an existing Hermes session (thread continuity)."""
    return render(
        "[Voice request from {user_name}, relayed by Speakeasy] ", names) + request + "\n\n" + render(
        "Begin your reply with exactly one line: \"Voice: ", names) + summary + render(
        "\" so this conversation shows what was asked, then do the work as you normally would here. "
        "Approval prompts cannot be answered from voice on this path: if a step needs {possessive_approval}, stop before "
        "it and ask {user_name} to confirm here. Lead with the outcome in one or two plain sentences.", names,
        possessive_approval=f"{names.possessive} explicit approval")


def thread_task_message(names: Names, request: str, summary: str, context: str) -> str:
    """The first message in a new chat thread opened for a voice task."""
    recent = context.strip()[-3000:]
    return (render("[Voice request from {user_name}, relayed by Speakeasy] ", names) + request
            + (f"\n\nRecent voice conversation for context:\n{recent}" if recent else "") + "\n\n"
            + render("Begin your reply with exactly one line: \"Voice: ", names) + summary + render(
                "\" so this thread shows what was asked, then do the work. This thread is where {user_name} will "
                "follow up, so write for reading here: lead with the outcome in one or two plain sentences. Approval "
                "prompts cannot be answered from voice: if a step needs {possessive_approval}, stop before it and ask "
                "{user_name} to confirm here in the thread.", names,
                possessive_approval=f"{names.possessive} explicit approval"))


def without_voice_header(text: str) -> str:
    """The reply minus its "Voice:" header line (the task list already names the task)."""
    return re.sub(r"^\s*(?:\U0001f399\ufe0f?\s*)?Voice:[^\n]*\n*", "", text or "", count=1).strip()


# -- email draft decisions (sent into the task's own session) ---------------------------

def draft_approved_message(names: Names, draft_json: str) -> str:
    return render(
        "{user_name_cap} approved exactly this email draft by pressing Approve on the email card. Send it now, "
        "unchanged, with your email tool, from the given account, then report the result in one line "
        "(sent, or the exact error). Do not change any field. End with DONE and SPOKEN lines as before.\n\n",
        names) + "```json\n" + draft_json + "\n```"


def draft_denied_message(names: Names) -> str:
    return render(
        "{user_name_cap} denied the email draft on the card. Do not send it. Discard the draft and reply in one line "
        "that it was discarded. End with DONE and SPOKEN lines as before.", names)


def draft_revise_message(names: Names, instructions: str) -> str:
    return render(
        "{user_name_cap} asked for changes to the email draft before approving it: ", names) + instructions.strip() + (
        "\nRewrite the draft accordingly. Do NOT send it. End your answer with a new fenced `email-draft` block "
        "(same JSON shape), then the DONE and SPOKEN lines.")


# -- live-call notices (appended to the running voice session) ---------------------------

def work_started_note(parallel: list[str]) -> str:
    note = "Work has started on this request. No action has been approved automatically."
    if parallel:
        note += " Still running in parallel: " + "; ".join(parallel) + "."
    return note


# Spoken by the server itself the moment work starts (appendSpeech), so the call is never silent
# while a task runs. First person, at most six words, never names a backend.
_ACK_NEW = ("On it.", "Looking now.", "Checking now.", "Let me look.", "On it, one sec.")
_PROGRESS_LEADS = ("Still on it:", "Quick update:", "Progress:")


def _pick(options: tuple[str, ...], seed: str) -> str:
    return options[int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % len(options)]


def short_task_name(name: str | None, words: int = 2) -> str:
    """The first words of a task name, for a line that must stay short (\"the Rome trip task\")."""
    picked = re.findall(r"[A-Za-z0-9#'&+-]+", name or "")[:words]
    return " ".join(picked) or "that"


def ack_new(seed: str) -> str:
    return _pick(_ACK_NEW, seed)


def ack_parts(count: int) -> str:
    words = {2: "two", 3: "three", 4: "four"}
    return f"On it, {words.get(count, str(count))} things at once."


def ack_follow_up(task_name: str | None) -> str:
    return f"Adding that to the {short_task_name(task_name)} task."


def ack_continuing(label: str | None) -> str:
    return f"Picking that up in {short_task_name(label, 3)}."


def ack_channel_thread(label: str) -> str:
    return f"Started that in a new {short_task_name(label, 1)} thread."


def ack_channel_post(label: str) -> str:
    return f"On it; results go to {short_task_name(label, 1)}."


def progress_line(seed: str, milestone: str) -> str:
    """A brief spoken progress update for a long task (the milestone is Hermes' own short line)."""
    text = re.sub(r"\s+", " ", milestone or "").strip().rstrip(".")
    words = text.split(" ")
    if len(words) > 10:
        text = " ".join(words[:10]) + "…"
    return f"{_pick(_PROGRESS_LEADS, seed)} {text}."


def clarify_channel(named: list[str], known: list[str]) -> str:
    """Asked instead of starting work: two channels were named, or one that is not set up."""
    if len(named) >= 2:
        return f"Should that go in {named[0]} or {named[1]}?"
    if known:
        return f"I don't have that channel. I have {', '.join(known[:4])}; which one?"
    return "I don't have that channel. Where should it go?"


def split_note(names: Names, parts: list[str]) -> str:
    return (f"{names.possessive[0].upper() + names.possessive[1:]} request was split into separate tasks that run in parallel: "
            + "; ".join(parts) + ". Results arrive one by one; say which part each answers.")[:2000]


def approval_note(names: Names) -> str:
    return (f"{names.assistant_name} needs explicit approval before continuing. "
            "Review Approve once or Deny in the panel.")


def draft_waiting_note(names: Names, subject: str | None, to: list[str]) -> str:
    about = f" \"{subject}\"" if subject else ""
    who = f" to {', '.join(to[:3])}" if to else ""
    return (f"An email draft{about}{who} is waiting on the card in the app. Say something like \"I drafted it — take a "
            f"look and approve when ready.\" It is sent only when {names.user} presses Approve on the card; a spoken "
            "approval does not send it. If they want changes, delegate the change as a follow-up to this task.")[:2000]


def draft_outcome_note(action: str) -> str:
    return {"approve": "The email draft was approved on the card; sending it now.",
            "deny": "The email draft was denied on the card and will not be sent.",
            "revise": "Revising the email draft as asked; the new draft will show on the card."}.get(action, "")


def result_for_part(part: str, spoken: str, more: bool) -> str:
    return f"Result for the part \"{part}\": {spoken}" + (" The other part is still working." if more else "")


def earlier_task_finished(names: Names, request: str, spoken: str) -> str:
    return (f"A separate, earlier task finished ({names.user_cap} asked: {request}). If {names.user} has since replaced "
            f"or corrected that request, mention only briefly that the old one finished. Otherwise tell them: {spoken}")[:2000]


def result_background(name: str | None, full: str | None, spoken: str | None) -> str | None:
    """The full answer of a finished task, as notes the voice model can answer follow-ups
    from (not read aloud). None when the full answer adds nothing to the spoken line."""
    text = re.sub(r"\s+", " ", safe_user_text(full or "", 4000) or "").strip()
    if not text or text == (spoken or "").strip():
        return None
    head = f"Background notes, not to read aloud. Full answer for the task \"{notice_text(name, 80) or 'that task'}\": "
    room = RESULT_BACKGROUND_CHARS - len(head)
    return head + (text if len(text) <= room else text[:room - 1].rstrip() + "…")


STOPPED_SPOKEN = "I stopped that task. I'm still here."
FAILED_SPOKEN = "I couldn't finish that one; the app shows what went wrong."
NO_TRANSCRIPT_SPOKEN = "I did not receive enough transcript to act. Please repeat the request."


def lost_track_note(names: Names) -> str:
    return f"I lost track of that task's progress, so approval and stop are blocked until {names.user} asks again."


def added_to_task_note(earlier: str) -> str:
    return f"Added that to the task already working on: {earlier}. It will come back as one answer."


def continuing_in_note(where: str) -> str:
    return f"Picking that up in the {where} conversation; the answer lands there too."


def continuing_failed_note(where: str) -> str:
    return f"I couldn't pick that up in the {where} conversation; the app shows what went wrong."


def ended_without_result(status: str) -> str:
    return f"That task ended without a result (status {status})."


# -- chat notices (delivery target) ------------------------------------------------------

def still_working_notice(request: str | None, short_status: str | None) -> str:
    text = f"Still working on: {notice_text(request, 140) or 'your request'}"
    status = notice_text(short_status, 80)
    return text + (f" ({status})" if status else "")


def needs_you_notice(names: Names, summary: str | None) -> str:
    return f"Needs you: {notice_text(summary, 300) or names.assistant_name + ' is waiting for your approval'}"


def stopped_notice(names: Names, status: str, request: str | None) -> str:
    reason = {"failed": "the work failed", "cancelled": "the work was cancelled",
              "interrupted": "the work was interrupted",
              "ambiguous": f"{names.assistant_name} lost track of the backend run (status unconfirmed)"}.get(status, status)
    about = notice_text(request, 140)
    return f"Stopped: {reason}" + (f" — {about}" if about else "")


def draft_notice(subject: str | None) -> str:
    return "Email draft waiting for your approval in Speakeasy" + (f": {notice_text(subject, 120)}" if subject else ".")
