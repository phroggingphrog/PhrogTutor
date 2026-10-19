# PhrogTutor

A personal AI tutor that learns how you think. It tracks what you understand from ordinary chat (no quizzes),
teaches each concept the way that worked for you before, and explains your own projects through their real code.

## What it does

PhrogTutor has its agent engine rebuilt on the [pi agent SDK](https://github.com/earendil-works/pi).

- **pi engine**: every DeepTutor mode (chat, solve, research, questions, visualize, co-writer, book, partners) runs as a pi agent session (`pi_engine/`); pi plugins load from `pi_engine/agent-dir/extensions`.
- **Learns from chat, not quizzes**: after each reply a cheap background call records what the learner showed (confusion, misconception, correct use), plus edited code they ran. Each concept gets a decaying mastery score. You can see and fix it on the **What I know** page.
- **Teaches by mastery**: weak topics get a worked example and then a part-done one; middle topics get one Socratic question; strong topics get a retrieval challenge. Topics are matched by meaning (embeddings). Rules that worked for the learner are written to `teaching_rules.md` and reused.
- **Runnable code**: Python, R and SQL blocks get a Run button. Notes work like a notebook, with shared variables per note, Run all and Restart. A terminal panel is included. All code runs in a bubblewrap sandbox with no network.
- **Notes**: an Obsidian-like Markdown editor (Tiptap) over plain `.md` files, with folders, maths, search, "Ask tutor", and extra read-only folders (`PI_TUTOR_NOTE_SOURCES`).

Install: `scripts/install.sh` (run `--check` first), then set the gateway in `.env` / `pi_engine/agent-dir/models.json`. All settings are in `.env.example`.

- **Project tours:** map any codebase (a folder or a git URL) and get guided tours that quote the real code; weak concepts are taught with examples from your own projects.
- **Notes:** Markdown notes with runnable Python/R/SQL cells, a sandboxed terminal, AI edit and Ask.
- **Mind Atlas:** an AI digest of where you stand, plus a study list of course topics vs what you know.

## Install

```bash
cp .env.example .env   # set your model gateway, keys and paths
scripts/install.sh
```

Every setting is in `.env.example`. Update checks are off unless you set `PI_TUTOR_UPDATE_REPO` to your own repository.

## Licence

Apache-2.0 (see `LICENSE`). pi-tutor is a modified fork of DeepTutor; the original copyright notices are kept in `LICENSE` as the licence requires.
