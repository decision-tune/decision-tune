---
title: DecisionTune 1.0
colorFrom: red
colorTo: gray
sdk: gradio
sdk_version: 6.29.1
app_file: app.py
license: apache-2.0
models:
  - decision-tune/decisiontune-1.0
short_description: A 395M decision model. Pick an option or get P(yes).
---

# DecisionTune 1.0 demo

Pick a question type, type a state and a question, and see the decision, a probability chart and the latency.

The Space runs the `decision_tune` package from [github.com/decision-tune/decision-tune](https://github.com/decision-tune/decision-tune) on a free CPU. The Space bundles a copy of `src/decision_tune` next to `app.py`, so the demo runs the same code as `pip install decision-tune`. Weights: [decision-tune/decisiontune-1.0](https://huggingface.co/decision-tune/decisiontune-1.0), Apache-2.0.
