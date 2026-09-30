"""Commands in answers: never spoken, and always copyable on their own."""
from speakeasy import text as T
from speakeasy.prompt import builder as P

SSH_KEY = (
    "Here's the line to run. The server can't add its own key to the laptop because the laptop isn't letting "
    "it in yet. Open Terminal on the laptop, paste this one line, and "
    "press return:\n\n"
    'mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo "ssh-ed25519 AAAAexamplekey server-hermes" >> '
    "~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys\n\n"
    "This isn't a password. It's the server's public key, which is safe to share.")

LAUNCH_AGENT = (
    "What's left is one command you'll need to run yourself.\n\n"
    "On the server, open the Terminal app and paste this:\n\n"
    "launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.example.dashboard.plist\n\n"
    "After that it will start at login.")


def test_bare_commands_are_split_out():
    pieces = T.split_commands(SSH_KEY)
    kinds = [k for k, _ in pieces]
    assert kinds == ["text", "command", "text"]
    assert pieces[1][1].startswith("mkdir -p ~/.ssh") and pieces[1][1].endswith("authorized_keys")
    assert [k for k, _ in T.split_commands(LAUNCH_AGENT)] == ["text", "command", "text"]


def test_fenced_command_comes_out_bare():
    pieces = T.split_commands("Run this:\n\n```bash\nbrew install jq\n```\n\nThen tell me.")
    assert pieces == [("text", "Run this:"), ("command", "brew install jq"), ("text", "Then tell me.")]


def test_sentences_are_not_commands():
    for line in ["Open Terminal on the laptop.", "Open the app and press Save.",
                 "Echo is a problem on speakerphone.", "Git history shows the fix landed Tuesday."]:
        assert not T.looks_like_command(line), line
    assert T.split_commands("The league team won by 12. Weather is sunny.") == [
        ("text", "The league team won by 12. Weather is sunny.")]


def test_voice_never_gets_the_command():
    notes = " ".join(P.result_notes("Laptop key", SSH_KEY, "Here's the line to run."))
    assert "mkdir" not in notes and "authorized_keys" not in notes and "command, shown in the app" in notes
    r = T.split_result("Done.\n\nSPOKEN: Paste `launchctl bootstrap gui/$(id -u) ~/x.plist` on the server.", ())
    assert "launchctl" not in r["spoken"] and "ready to copy" in r["spoken"]
    r = T.split_result(LAUNCH_AGENT + "\nSPOKEN: One command left to run on the server; it's ready to copy.", ())
    assert r["spoken"] == "One command left to run on the server; it's ready to copy."


def test_chat_delivery_posts_each_command_alone():
    from speakeasy.calls import Notices as NoticeBoard
    sent = []

    class Store:
        def claim_notice(self, key):
            return True

    board = NoticeBoard.__new__(NoticeBoard)
    board.store, board.notifier = Store(), object()
    board.target = lambda: "discord:1"

    class Q:
        def put(self, item):
            sent.append(item)
    board.queue = Q()
    board.answered("r1", SSH_KEY, "discord:1")
    assert [t for _, _, t in sent][1].startswith("mkdir -p ~/.ssh") and len(sent) == 3
    sent.clear()
    board.commands("r2", LAUNCH_AGENT, "discord:1:99")
    assert len(sent) == 1 and sent[0][1] == "discord:1:99" and sent[0][2].startswith("launchctl bootstrap")
    sent.clear()
    board.answered("r3", "The league team won by 12.", "discord:1")
    assert len(sent) == 1


def test_prompts_ask_for_fenced_commands():
    names = P.Names() if hasattr(P, "Names") else None
    assert "fenced code block" in P.COPYABLE_RULE
