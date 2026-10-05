# AGENTS.md

## DecisionTune for AI agents

DecisionTune 1.0 is a local decision model. It picks one option from a list you give it, or returns P(yes) for a yes/no question. Use it to route, triage, classify, select tools and run yes/no checks, on this computer.

Before you install, call or prompt it, read the skill:

- In this repo: [skills/decisiontune/SKILL.md](skills/decisiontune/SKILL.md)
- On the web: https://raw.githubusercontent.com/decision-tune/decision-tune/main/skills/decisiontune/SKILL.md

Quick facts:

- Install: `pip install decision-tune` (Apple silicon: `pip install "decision-tune[mlx]"`, Python 3.11+).
- Non-interactive use: run `decisiontune download --yes` once, or set `DECISION_TUNE_YES=1`. Otherwise the first call fails because nobody can answer the download prompt.
- One-off decision: `decisiontune ask "<QUESTION>" --state "<TEXT>" --option "<OPTION 1>" --option "<OPTION 2>" --json`. Leave out `--option` for yes/no.
- Write each option as a short description, not a bare label.
