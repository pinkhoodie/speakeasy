# Speakeasy for Codex and Claude Code users

Status: design, not built. Backends in progress on branches `backend-codex` and `backend-claude-code`
(contract: `plugin/speakeasy/backends/base.py`).

## Who this is for

Someone who builds apps and products with Codex or Claude Code and wants to drive that work by voice:
start tasks, keep talking while they run, hear progress, approve what matters, and ship the result.
They don't want email cards, daily life briefs, memory about their dinner plans, or home control. They
want engineering throughput.

So the question isn't "which Hermes features can we fake without Hermes". It's "what does each Hermes
feature do for a Hermes user, and what's the coding equivalent". Most map to something better.

## The mapping

| Hermes feature | What it's for | Coding equivalent |
| --- | --- | --- |
| Results posted to a chat / channels | Results land where you'll see them | **Results land as a branch or PR.** Each task ends as a commit on its own branch, optionally a draft PR with the task's summary as the body. |
| Channel routing ("put this in #work") | The right conversation gets the task | **Project routing.** "In the web app, fix the login redirect" goes to the right repo folder; the user registers projects with a name and a one-line topic, exactly like channels today. |
| New thread per task | Tasks don't tangle | **One git worktree per task.** Parallel voice tasks each get their own worktree + branch, so two tasks never edit the same files at once. This is the coding version of the thread, and it's the property people actually need. |
| Continue my X conversation | Pick up past work by name | **Resume a past agent session by name.** "Keep going on the auth refactor" resumes that Codex thread / Claude session (`thread/list` + `thread/resume`; `~/.claude/projects` + `--resume`). |
| Email draft + Send card | Review a consequential artifact, then act | **Ship card.** When a task finishes: files changed, +/- lines, test result, and buttons **Commit**, **Open PR**, **Discard**. Waits across hang-ups until acted on, like drafts do today. |
| Approval rows | Say yes/no to risky actions | **Command and file-change approvals** (native to both backends): "Run `npm install stripe`", "Edit `src/auth.ts`". Plus an allow-list per project so routine commands (tests, lint) never ask. |
| Product cards | See the thing, not a description | **Preview card.** Dev server URL and a screenshot of the running app after a UI task; test summary card (42 passed, 1 failed + the failing name) after a test task. |
| Daily brief | Start the day knowing what matters | **Repo brief at call start** (opt-in): what changed since the last call, CI status on your branches, open PRs waiting on you, tasks that finished while you were away. |
| Memory | The assistant knows your context | **Project memory is already native**: both tools read `AGENTS.md` / `CLAUDE.md`. Speakeasy adds per-project task history ("last time: migrated the users table, PR #41 merged") fed into the next task's prompt. |
| Notices when a call isn't connected | Don't miss results | Same notices: task done, needs approval, PR opened, CI failed. |
| — (new) | | **CI watch.** After Open PR, Speakeasy watches checks and speaks the result on the call, or notifies if you've hung up. |

Dropped for coding users: email, daily life brief, home control, chat delivery. Nothing to replace;
nobody in this audience asked for them.

## What this means for the build

1. **Backends** (in progress): Codex app-server and Claude Code stream-json, both behind `TaskBackend`.
2. **Projects** replace channels for coding backends: `settings.projects = [{name, path, topic}]`. The
   router already does topical channel choice; reuse it with projects as the options. `needs_workspace`
   capability triggers this.
3. **Worktree per task**: before `start_run`, create `.speakeasy/worktrees/<task>` on branch
   `speakeasy/<slug>` from the project's HEAD; the backend's workspace is that worktree. Follow-ups reuse
   it. Cleanup on Discard or after merge.
4. **Ship card**: new card type driven by `files.changed` + the terminal event. Commit/Open PR run in
   Speakeasy (git + `gh`), not in the agent, so they're deterministic and need the user's tap.
5. **Preview/test cards**: parse the final answer for a dev URL / test summary (same fenced-block pattern
   as `email-draft` and product cards), screenshot via headless browser when a URL is present.
6. **Repo brief + CI watch**: `gh` based, optional, off by default.
7. **Standalone server**: no Hermes to host the plugin, so `speakeasy serve` runs the same `server.py` /
   `service.py` on its own with a pairing command. Small model calls (titles, request tidy-up, routing)
   go through the Codex app-server (both audiences have a ChatGPT sign-in for the voice anyway).

Order after the backends land: standalone server → projects + worktree per task → ship card →
preview/test cards → repo brief and CI watch.

## Open questions

- Terms: confirm both tools allow being driven by a third-party app with the user's own sign-in.
- Codex app-server is marked experimental: pin a tested version range and keep a contract test.
- Claude Code users still need a ChatGPT sign-in or OpenAI key for the voice itself.
