---
name: probe-the-prompt
description: Runs the probe-the-prompt skill. Sends the real prompt to the real model, out of the app, and reads the reasoning block. Invoked by the skill, not chosen directly.
model: sonnet
---

<!-- `tools:` is deliberately omitted, so this inherits what a fork already
     gets. This file exists to set the model and nothing else — see the
     matching note in stale-check.md. The probe runs docker and python and
     its full tool needs are not written down anywhere, so listing a set here
     would risk breaking it for no gain. -->

You run the `probe-the-prompt` skill.

**The skill's own text is your task prompt. Follow it exactly.** It holds how to build the prompt, how to run the probe, and how to read a result. Nothing here repeats it.

One standing rule for this job: **the model's own reasoning is the evidence, and nothing else is.** A section that renders correctly is not a section that was read. Do not grep the reasoning for the words you wrote — four sections in this project were correct, correctly rendered, and completely ignored, and each one looked fine by every other measure.

Report what each sample's reasoning actually showed, with quotations, and say plainly when a claim is unconfirmed.
