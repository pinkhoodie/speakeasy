Write a **voice brief** for Speakeasy, the voice app I use to talk to you from my Mac.

A separate realtime voice model answers when I talk. It cannot see your memory, skills, tools or files. Everything it knows about me and about you at the start of a call comes from this brief. It is a third-party model, so write the brief knowing it leaves this machine.

Use what you already know: your memory, my user profile, your persona, and your real, currently enabled tools, skills, toolsets and connected platforms. Do not run tools to dig for new information and do not ask me questions; write it from what you have now. Do not take any action.

Write plain Markdown with exactly these five sections, in this order, using these headings:

## User
My name and how I like to be addressed, my time zone, the languages I speak. Only what helps a conversation.

## Assistant persona
Your name and personality as I know you: tone, humor, how direct you are, anything I have asked you to be or not be when talking with me.

## Capability map
The most important section. In plain spoken words, what you can actually do for me through your real tools, skills, connected platforms and integrations ("I can check your calendar, send Telegram messages, control the living-room lights, search your notes"). Group related abilities. Also list clearly what you cannot do or are not set up for, so the voice model never offers it. Never name API keys, account IDs, hostnames or file paths. Write it in the first person, as yourself ("I can…"). Don't describe the voice model as separate from you or tell it to pass work on to you: Speakeasy handles that on its own, and the voice speaks as you.

## Answer preferences
How I like answers: length, tone, units, formats, things that annoy me.

## Current context
Active projects or recurring topics, only at the level needed to understand a reference ("the kitchen remodel", "the conference talk"). One line each.

Hard rules:
- Never include secrets or sensitive data: no passwords, API keys, tokens, account or card numbers, street addresses, phone numbers, email addresses, health or financial details, or other people's private information.
- Leave out rules that only matter for how you work behind the scenes (restarts, internal tooling, how you store things); keep what changes a conversation.
- About 1,500 words at most. Short sentences. No preamble and no closing remarks: output only the five sections.
- End your answer with two last lines: `DONE: Voice brief written` then `SPOKEN: I wrote your voice brief.`
