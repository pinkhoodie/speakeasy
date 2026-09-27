Suggest where my voice tasks should go. Speakeasy, the voice app I use to talk to you from my Mac, can send each task to a different chat depending on what it is about.

Here are the chats you can post to (target: label):
{destinations}

Use what you already know about me and how I work: your memory, my user profile, and the conversations we usually have in these chats. Do not run tools, do not ask me questions, and do not take any action. Only read what you have now.

Propose up to 5 channels. Choose ONLY targets from the list above, copied exactly. For each give:
- target: the exact target from the list
- label: a short name as I'd say it out loud (like "#build" or "family")
- topic: a short plain description of what kinds of requests belong there (under 15 words)
- new_thread: whether each task should open its own thread there — {threads}

Skip chats that don't fit any clear kind of work. Reply with strict JSON only, a single array, no prose:
[{"target": "...", "label": "...", "topic": "...", "new_thread": false}]
