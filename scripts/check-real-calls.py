#!/usr/bin/env python3
"""Fail if the repo contains a sentence you actually said (or were told) on a voice call.

Reads the local Speakeasy and Hermes state on this machine (never committed) and looks for any
run of 6+ consecutive words (5 for short requests) from a real call inside tracked files. Skips quietly on machines
without Speakeasy state.
"""
import os, re, sqlite3, subprocess, sys
from pathlib import Path

HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
N = 6

def words(t):
    return re.findall(r"[a-z0-9']+", t.lower())

def phrases():
    """Only words from real calls: what you asked, what the voice/Hermes answered you, and call
    transcripts. Not Hermes's coding sessions (those quote this repo and would match everything)."""
    texts = []
    db = HOME / "speakeasy" / "state.sqlite3"
    if db.exists():
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        for (t,) in c.execute("select title from runs"):
            texts.append(t or "")
        for (t,) in c.execute("select text from work_events where kind in ('request','result','milestone')"):
            texts.append(t or "")
    log = HOME / "speakeasy" / "call-log.jsonl"
    if log.exists():
        import json
        for line in log.read_text(errors="ignore").splitlines():
            try:
                call = json.loads(line)
            except ValueError:
                continue
            for turn in call.get("turns", []) or []:
                texts.append(turn.get("text", "") if isinstance(turn, dict) else str(turn))
    grams = set()
    for t in texts:
        w = words(t)
        for i in range(len(w) - N + 1):
            g = w[i:i + N]
            if sum(len(x) > 3 for x in g) >= 3:   # skip filler like "i want to be able to"
                grams.add(" ".join(g))
    return grams

def main():
    if not (HOME / "speakeasy").exists():
        return 0
    grams = phrases()
    if not grams:
        return 0
    allow = set()
    af = Path.home() / ".config" / "speakeasy" / "call-phrase-allow.txt"
    if af.exists():
        allow = {l.strip().lower() for l in af.read_text().splitlines() if l.strip()}
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.split("\n")
    bad = 0
    for f in files:
        if not f or f.startswith("scripts/"):
            continue
        try:
            text = Path(f).read_text(errors="ignore")
        except (OSError, IsADirectoryError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            w = words(line)
            ok_line = " ".join(w)
            for a in sorted(allow, key=len, reverse=True):  # Speakeasy's own built-in wording, not yours
                ok_line = ok_line.replace(a, " ")
            w = ok_line.split()
            for i in range(len(w) - N + 1):
                g = " ".join(w[i:i + N])
                if g in grams:
                    print(f"FOUND words from a real call: {f}:{n}")
                    bad += 1
                    break
    return 1 if bad else 0

if __name__ == "__main__":
    sys.exit(main())
