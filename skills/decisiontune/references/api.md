# DecisionTune 1.0 API reference

Everything here is in `decision-tune` 1.0.0 (`pip install decision-tune`). Both command names work: `decision-tune` and `decisiontune`.

## Contents

- [CLI](#cli)
- [Python](#python)
- [HTTP](#http)
- [MCP](#mcp)
- [Recipe JSON](#recipe-json)
- [Environment variables](#environment-variables)
- [Error messages](#error-messages)

## CLI

`decisiontune [--version] <command> ...`

Flags shared by `download`, `ask`, `serve`, `app`, `run` and `mcp`:

| Flag | Meaning |
|---|---|
| `--model MODEL` | Hugging Face repo id or local directory. Default `decision-tune/decisiontune-1.0`. |
| `--backend {auto,torch,onnx,mlx}` | `auto` = MLX on Apple silicon if installed, else PyTorch. `onnx` needs `decision-tune[onnx]`. |
| `--yes` | Download the model without asking. |

`--device DEVICE` (on `ask`, `serve`, `app`, `run`): PyTorch device `cpu`, `cuda` or `mps`. Default: `cuda` if available, else `cpu`.

### `download`

Download and verify the model files. Prints the local path.

```bash
decisiontune download --yes
```

### `ask QUESTION`

| Flag | Meaning |
|---|---|
| `--state STATE` | The text to decide on. Default empty. |
| `--option OPTION` | One option. Repeat for each. Leave out for a yes/no question. The option text is also the returned key. |
| `--json` | Print JSON. |

```bash
decisiontune ask "Which team handles this?" --state "I was charged twice this month." --option billing --option shipping --option "tech support" --json
decisiontune ask "Is the customer asking for a refund?" --state "The order arrived broken. I want my money back." --json
```

`--json` output: `{"choice": str, "probabilities": {str: float}, "confidence": float}`, or `{"answer": "yes"|"no", "p_yes": float}` for yes/no.

Text output: `<choice>  (confidence <c>)` then one `<p>  <option>` line per option, highest first; or `<answer>  (P(yes) = <p>)`.

### `run RECIPE INPUT`

Run a recipe on every row of a `.csv` or `.xlsx` file (first sheet, first row is the header), or a folder of `.txt` and `.md` files (columns `file` and `text`).

| Argument / flag | Meaning |
|---|---|
| `RECIPE` | A recipe name (see `recipes`) or a path to a `.json` file. |
| `INPUT` | `.csv`, `.xlsx` or a folder. |
| `-o, --output OUTPUT` | Result file, `.csv` or `.xlsx`. Default: `<input name>-decided.csv` next to the input. |
| `--force` | Replace the output file if it exists. |

Prints `<n> rows, <k> need review, <ms> ms. Wrote <path>`. Failed rows are reported on stderr and marked in the `error` column; the run goes on.

### `recipes`, `recipe show NAME`, `recipe new NAME`

- `recipes`: one line per recipe: name, source (`built-in` or `user`), question names.
- `recipe show NAME`: print the recipe as JSON.
- `recipe new NAME`: save a copy of `support-triage` as `~/.decision-tune/recipes/NAME.json` (or under `$DECISION_TUNE_HOME/recipes`). Prints the path. Edit that file.

A user recipe with the same name as a built-in one replaces it.

### `serve`

| Flag | Meaning |
|---|---|
| `--host HOST` | Address to listen on. Default `127.0.0.1` (this computer only). |
| `--port PORT` | Default `8000`. |

Prints `DecisionTune 1.0 (<backend>) on http://127.0.0.1:8000/` when ready.

### `app`

Opens the browser app. `--port PORT` is the first port to try (default `8000`); it tries the next 10 ports if it is busy. `--no-browser` does not open the browser.

### `mcp`

MCP server over stdio. `--allow DIR` limits reads and writes to that folder (repeatable). See [MCP](#mcp).

## Python

```python
from decision_tune import DecisionModel, Recipe, list_recipes, download, verify, REPO, __version__
```

### `DecisionModel.from_pretrained(repo_or_dir=REPO, backend="auto", device=None, dtype="float32", yes=None)`

Loads from the Hub (default `REPO == "decision-tune/decisiontune-1.0"`) or a local directory. Checks every file against `manifest.json` first. `yes=True` downloads without asking. Attributes: `backend_name`, `device`.

### `m.choose(state, question, options) -> dict`

- `state`: a string or a dict (shown to the model as `key: value | key: value`).
- `question`: a string.
- `options`: a list of strings, or a dict `{key: description}`. The model reads the description; you get the key back.
- Returns `{"choice": key, "probabilities": {key: float, ...}, "confidence": float}`. `confidence == probabilities[choice]`.

### `m.yes_no(state, question) -> float`

Returns P(yes) as a float. It is not a dict.

### `Recipe`

```python
r = Recipe.load("support-triage")        # a name or a path to a .json file
r = Recipe.from_dict({...})              # validate a dict
results = r.run(rows, model=m)           # rows: list of dicts; one dict per row
r.to_dict(); r.save()                    # save() writes to ~/.decision-tune/recipes/<name>.json
```

`run(rows, model=None, progress=None, failed=None)`: with `model=None` it loads the default model once and reuses it. `progress(i, n)` is called after each row. Pass a list as `failed` to get the positions of rows that failed. Each result is the input row plus the answer fields (see [Recipe JSON](#recipe-json)).

`list_recipes()` returns `[{"name", "source", "questions", "path"}, ...]`.

## HTTP

Start with `decisiontune serve`. Base URL `http://127.0.0.1:8000`. Every POST needs `Content-Type: application/json` (or `text/csv` where noted) and a `Content-Length`. Errors are `{"error": "<message>"}` with an HTTP status code. The server runs one model call at a time.

### `POST /decide`

Request:

```json
{"state": "My package never arrived.",
 "question": "Which team should handle this customer message?",
 "options": {"billing": "Billing: charges, refunds, invoices",
             "shipping": "Shipping: delivery, lost or damaged packages",
             "tech": "Tech support: bugs, crashes, login problems"}}
```

- `state`: string or object. Default `""`.
- `question`: non-empty string. Required.
- `options`: a list of strings or a `{key: description}` object. Leave it out (or send `[]` or `{}`) for yes/no.

Response, choose: `{"choice", "probabilities", "confidence", "ms"}`. Response, yes/no: `{"answer": "yes"|"no", "p_yes": float, "ms": float}`.

`POST /` accepts the same body.

### `POST /recipes/<name>/run`

Body: `{"rows": [{"subject": "...", "message": "..."}]}` with `Content-Type: application/json`, or a CSV text body with `Content-Type: text/csv`.

Response: `{"columns": [...], "rows": [...], "count": int, "needs_review": int, "failed": [row positions], "ms": float, "run_id": str}`.

### Other routes (used by the app)

| Route | Purpose |
|---|---|
| `GET /api/status` | `{"model": "DecisionTune 1.0", "backend": str, "version": str}` |
| `GET /api/recipes` | The `list_recipes()` list. |
| `GET /api/recipes/<name>` | One recipe as JSON. |
| `POST /api/recipes` | Body: a recipe object. Saves it. Returns `{"saved": name}`. |
| `POST /api/run` | Body: `{"recipe": name or recipe object, "input": {"type": "csv"|"xlsx"|"lines"|"rows", "data": ...}}`. `xlsx` data is base64; `rows` data is a list of objects; `lines` gives one row per line in column `text`. Same reply as the recipe route. |
| `POST /api/preview` | Body: `{"input": {...}}` as above. Returns `{"columns", "count", "text"}`. |
| `GET /api/export?run=<run_id>&format=csv|xlsx` | Download the rows of a recent run. |
| `POST /api/export` | Body: `{"columns", "rows", "format": "csv"|"xlsx"}`. Returns the file. |

## MCP

Config:

```json
{"mcpServers": {"decisiontune": {"command": "decisiontune", "args": ["mcp", "--allow", "/Users/you/Documents/decisions"]}}}
```

Server name `decisiontune`. The model loads on the first tool call that needs it.

| Tool | Arguments | Result (`structuredContent`) |
|---|---|---|
| `decide` | `state` (string or object, required), `question` (string, required), `options` (list or `{key: description}`, optional; leave out for yes/no) | `{"choice", "probabilities", "confidence", "ms"}` or `{"answer", "p_yes", "ms"}` |
| `run_recipe` | `recipe` (name, `.json` path or recipe object, required); exactly one of `input_path` (`.csv`, `.xlsx` or folder) or `rows` (list of objects); `output_path` (`.csv` or `.xlsx`, optional) | `{"count", "needs_review", "ms", "output_path"?, "rows", "truncated"}` |
| `list_recipes` | none | `{"recipes": [...]}` |

`run_recipe` returns only the first rows to the assistant (`truncated` is true when it cut some); `output_path` gets all of them. It replaces an existing output file only if the name ends with `-decided.csv` or `-decided.xlsx`.

Tool errors come back with `isError: true` and a one-line message.

## Recipe JSON

```json
{
  "name": "support-triage",
  "read": ["subject", "message"],
  "questions": [
    {"name": "team", "type": "choose", "question": "Which team should handle this customer message?",
     "options": {"billing": "Billing: charges, refunds, invoices",
                 "shipping": "Shipping: delivery, lost or damaged packages",
                 "tech": "Tech support: bugs, crashes, login problems"}},
    {"name": "refund", "type": "yes_no", "question": "Is the customer asking for a refund or an exchange?"},
    {"name": "tone", "type": "scale", "question": "What is the tone of the customer?",
     "levels": {"neg": "Negative: the customer is unhappy or upset",
                "neu": "Neutral: a plain request or question",
                "pos": "Positive: the customer is happy or thankful"}}
  ],
  "review_below": 0.6
}
```

| Field | Rule |
|---|---|
| `name` | Lowercase letters, digits, `_` and `-`; starts with a letter or digit. Question names follow the same rule and must be unique. |
| `read` | Column names that form the state. One column: the state is that cell's text. Several: a dict of those columns. Empty: all columns. The input must have every listed column. |
| `questions[].type` | `choose` (needs `options`), `yes_no`, or `scale` (needs `levels`, same shape as `options`). |
| `questions[].question` | Non-empty text. |
| `review_below` | A number from 0 to 1. Default 0.6. |

Output fields per question `q`: `q` (the answer key, or `yes`/`no`), `q_confidence`, and for `yes_no` also `q_p_yes`. For `yes_no`, the confidence is the larger of P(yes) and P(no). Every row also gets `needs_review` (any confidence below `review_below`, or the row failed) and `error` (the reason, only on a failed row).

Output files put an apostrophe before any cell that starts with `=`, `+`, `-` or `@`, so no cell runs as a formula.

## Environment variables

| Variable | Effect |
|---|---|
| `DECISION_TUNE_YES=1` | Download the model without asking. |
| `DECISION_TUNE_ROOTS` | Allowed folders for `mcp`, separated by `:` on Mac and Linux. Adds to `--allow`. |
| `DECISION_TUNE_HOME` | Folder for user recipes (`<home>/recipes`). Default `~/.decision-tune`. |

## Error messages

The CLI prints errors as one line: `decision-tune: <message>`.

| Message | Cause and fix |
|---|---|
| ``DecisionTune 1.0 is not downloaded yet. Run `decision-tune download --yes`, set DECISION_TUNE_YES=1, or pass yes=True.`` | No terminal to ask for consent. Give consent. |
| ``Run `decisiontune download` first.`` | MCP server without the model. Run the download, or start it with `mcp --yes`. |
| `Download cancelled.` | Someone answered no at the prompt. |
| `SHA-256 mismatch for <file> in <path>: refusing to load` | A model file is damaged or changed. Delete it from the Hugging Face cache and download again. |
| `need at least 2 options` | Python `choose` with fewer than 2 options. |
| `options must be 2 to 32 different, non-empty texts (a list or a {key: description} object)` | CLI, HTTP or MCP options are empty, repeated, or too many. |
| `sequence of <n> tokens exceeds the 8192-token context limit` | The question, options and state are too long. Shorten the state. |
| `no recipe '<name>'; available: <names>` | Unknown recipe name. Run `decisiontune recipes`. |
| `missing columns: <names>` | The input lacks a column the recipe reads. |
| `<path> already exists: pass --force to replace it, or -o for another name` | `run` will not replace an output file by default. |
| `review_below must be a number from 0 to 1` | Bad recipe field. |
| `questions must be a list of 1 to 50 questions` | Bad recipe field. |
| `send Content-Type application/json or text/csv` (HTTP 415) | Add the `Content-Type` header. |
| `this host name is not allowed` (HTTP 403) | Call `127.0.0.1` or `localhost`, or start `serve --host` with that address. |
| `cross-origin requests are not allowed` (HTTP 403) | A browser page from another origin called the server. |
| `send a Content-Length header` (HTTP 411) | Send the body with a length. |
| `question must be non-empty text` (HTTP 400) | Add `question`. |
| `the model could not decide this input` (HTTP 500) | The model raised. Most often the input is too long. |
| `Path is outside the allowed folders: <path>. Add it with --allow.` | MCP path outside `--allow`. |
| `give exactly one of input_path or rows` | MCP `run_recipe` needs one input. |
| `'<path>' already exists and will not be overwritten; use a new name, or one ending in -decided.csv` | MCP `output_path` would replace a file. |
