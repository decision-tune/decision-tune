---
name: decisiontune
description: Run, call and prompt DecisionTune 1.0, a small local decision model that picks one option from a list you give it, or returns P(yes) for a yes/no question, with a probability for each answer. Use it when code or an agent must route, triage or classify text (support tickets, emails, intents), select a tool or next step, answer a yes/no check or guardrail, rank options, label every row of a CSV or Excel spreadsheet or a folder of text files, or make any of these decisions locally, offline and privately on a laptop CPU or Apple silicon. Covers install (pip, uv, MLX), the Python API, the CLI, the local HTTP endpoint, the MCP server, recipes for batch runs, reading probabilities and needs_review, and how to write questions and options that work. Do not use it to generate, summarize or translate text, or for math and multi-step reasoning.
license: Apache-2.0
---

# DecisionTune 1.0

DecisionTune 1.0 is a 395M decision model. You give it a **state** (the text or fields to judge), a **question** and **options**. It picks one option and returns a probability for each. With no options, it answers a yes/no question and returns P(yes). It does one encoder pass. It runs on your machine. Nothing is sent anywhere.

**It does not generate text.** It only scores the options you give it.

Use it for: routing, triage, intent and topic labels, tool selection, yes/no checks and guardrails, ranking a short list, labeling rows of a spreadsheet.

Do not use it for:

- Writing, summarizing, extracting or translating text. It cannot produce new text.
- Math, dates, counting or multi-step reasoning. Do that in code.
- Non-English text. It is English only.
- High-risk decisions about people (hiring, credit, housing, health, legal) without independent validation.

More detail: [references/prompting.md](references/prompting.md) (how to write questions and options) and [references/api.md](references/api.md) (every flag, route, field and error).

## Install

```bash
pip install decision-tune                # PyTorch backend (the default)
pip install "decision-tune[mlx]"         # Apple silicon: adds MLX, selected automatically
pip install "decision-tune[onnx]"        # adds onnxruntime; use --backend onnx
uv tool install "decision-tune[mlx]"     # or install the CLI with uv
uvx decision-tune --version              # or run it once without installing
```

MLX needs Python 3.11 or newer. Both command names work: `decision-tune` and `decisiontune`.

### Model download (agents: read this)

The package is small. The weights (1.58 GB, Apache-2.0) download from Hugging Face on first use. The package then checks every file against a SHA-256 manifest. In a terminal, it asks `[Y/n]` first. **An agent has no terminal, so give consent explicitly**, or the call fails with:

```text
DecisionTune 1.0 is not downloaded yet. Run `decision-tune download --yes`, set DECISION_TUNE_YES=1, or pass yes=True.
```

Give consent once:

```bash
decisiontune download --yes              # once, before anything else
export DECISION_TUNE_YES=1               # or: let any later call download without asking
```

Ask the user before you download 1.58 GB if they have not already agreed to it.

## Way 1: Python (best for code you are writing)

```python
from decision_tune import DecisionModel

m = DecisionModel.from_pretrained()   # load once, reuse; add yes=True to allow the download without a prompt

r = m.choose(
    "My package never arrived.",                          # state: a string or a dict of fields
    "Which team should handle this customer message?",    # question
    {"billing": "Billing: charges, refunds, invoices",    # options: {key: description} or a list of strings
     "shipping": "Shipping: delivery, lost or damaged packages",
     "tech": "Tech support: bugs, crashes, login problems"},
)
# r == {"choice": "shipping", "probabilities": {"billing": ..., "shipping": ..., "tech": ...}, "confidence": ...}

p = m.yes_no("The order arrived broken. I want my money back.", "Is the customer asking for a refund?")
# p is a float: P(yes). Here it is near 1.
```

Signatures:

- `DecisionModel.from_pretrained(repo_or_dir="decision-tune/decisiontune-1.0", backend="auto", device=None, dtype="float32", yes=None)`
- `m.choose(state, question, options) -> {"choice": key, "probabilities": {key: float}, "confidence": float}`
- `m.yes_no(state, question) -> float` (a bare number, not a dict)

With a dict of options, the model reads the description and you get the key back. With a list, the key is the text itself. A dict state is shown to the model as `key: value | key: value`.

## Way 2: CLI (best for one-off checks and shell scripts)

```bash
# Pick one option. Each --option is one choice; write it as a short description.
decisiontune ask "Which team should handle this customer message?" \
  --state "My package never arrived." \
  --option "Billing: charges, refunds, invoices" \
  --option "Shipping: delivery, lost or damaged packages" \
  --option "Tech support: bugs, crashes, login problems" --json

# Yes/no: leave out --option.
decisiontune ask "Is the customer asking for a refund?" --state "The order arrived broken. I want my money back." --json
```

`--json` prints `{"choice", "probabilities", "confidence"}`, or `{"answer", "p_yes"}` for yes/no. Without `--json`, it prints the choice and a sorted probability list, or `yes  (P(yes) = ...)`. On the CLI the option text is also the returned key.

Batch work uses a recipe (a JSON file of questions, see below):

```bash
decisiontune recipes                                  # list recipes (support-triage is built in)
decisiontune recipe show support-triage               # print one as JSON
decisiontune recipe new my-recipe                     # copy to ~/.decision-tune/recipes/my-recipe.json to edit
decisiontune run support-triage tickets.csv           # writes tickets-decided.csv next to the input
decisiontune run my-recipe.json notes/ -o out.xlsx    # a .json path also works; a folder of .txt/.md gives columns file, text
decisiontune app                                      # browser app for people (not for agents)
```

`run` refuses to replace an existing output file unless you pass `--force`.

## Way 3: HTTP (best for services in any language)

```bash
decisiontune serve --yes                 # http://127.0.0.1:8000/decide, localhost only
```

```bash
curl -s http://127.0.0.1:8000/decide -H 'Content-Type: application/json' \
  -d '{"state": "My package never arrived.", "question": "Which team should handle this customer message?",
       "options": {"billing": "Billing: charges, refunds, invoices", "shipping": "Shipping: delivery, lost or damaged packages", "tech": "Tech support: bugs, crashes, login problems"}}'
# {"choice": "shipping", "probabilities": {...}, "confidence": ..., "ms": ...}

curl -s http://127.0.0.1:8000/decide -H 'Content-Type: application/json' \
  -d '{"state": "The order arrived broken. I want my money back.", "question": "Is the customer asking for a refund?"}'
# {"answer": "yes", "p_yes": ..., "ms": ...}

curl -s http://127.0.0.1:8000/recipes/support-triage/run -H 'Content-Type: application/json' \
  -d '{"rows": [{"subject": "Broken mug", "message": "The order arrived broken. I want my money back."}]}'
# {"columns": [...], "rows": [...], "count": 1, "needs_review": ..., "failed": [], "ms": ..., "run_id": ...}
```

Always send `Content-Type: application/json` (or `text/csv` for a CSV body to the recipe route). Otherwise you get HTTP 415. Errors come back as `{"error": "..."}`. The server runs one model call at a time. Start it in the background and stop it when you are done. For a single question, use `ask` instead of starting a server.

## Way 4: MCP (best when the agent itself should decide)

Download the model first (`decisiontune download --yes`), then add to the client's MCP config:

```json
{
  "mcpServers": {
    "decisiontune": {"command": "decisiontune", "args": ["mcp", "--allow", "/Users/you/Documents/decisions"]}
  }
}
```

Tools:

- `decide` with `{"state", "question", "options"?}`. Leave out `options` for yes/no.
- `run_recipe` with `{"recipe", "input_path" | "rows", "output_path"?}`. Give exactly one of `input_path` or `rows`.
- `list_recipes` with `{}`.

`--allow DIR` (repeatable, or `DECISION_TUNE_ROOTS`) limits file access to those folders. Without it, the tools can read any file the user can. If the command is not found, use the full path from `which decisiontune`. If the model is missing, the tool returns the error "Run `decisiontune download` first." (or start it with `mcp --yes`).

## Reading the results

- `choice`: the option key with the highest probability. `confidence`: that probability.
- `probabilities`: a softmax over your options. They sum to 1 and are **not calibrated**. Use them to rank and to set thresholds, not as true odds.
- `p_yes`: P(yes). `answer` is `yes` when `p_yes >= 0.5`.
- Results depend on the option set. Adding, removing or reordering options can change the answer.
- Recipes add `<question>_confidence` per answer (for yes/no also `<question>_p_yes`, and confidence is the larger of P(yes) and P(no)). `needs_review` is true when any confidence is below `review_below` (default 0.6) or the row failed. Send those rows to a person.
- Pick thresholds from the stakes and your own labeled rows. Low-cost actions can act on lower confidence. Costly or irreversible actions need a higher bar and a review path. Check the threshold on real data before you ship it.

## Performance

- Load the model once and reuse it. Loading is the slow part. A decision is fast: the median request in our index run took 25.9 ms on a laptop. On Apple silicon with MLX, a short decision takes about 10 ms.
- `--backend auto` picks MLX on Apple silicon when the MLX extra is installed, else PyTorch. Force one with `--backend torch|mlx|onnx` or `backend="..."`.
- Each question is one sequence of at most 8,192 tokens. Longer input is refused, not truncated. Keep states short; most training text was shorter than 4,096 tokens.
