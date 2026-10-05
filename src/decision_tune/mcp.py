"""MCP server over stdio for Claude Desktop, Cursor and any MCP client: newline-delimited JSON-RPC 2.0, stdlib only.

    run_stdio(DecisionModel.from_pretrained)

stdout carries protocol messages only; everything else goes to stderr. The model loads on the first tool call that needs it.
"""
import contextlib
import json
import os
import sys
import time
import traceback

from . import __version__
from .recipe import Recipe, list_recipes, read_rows, write_csv, write_xlsx

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = "DecisionTune picks an option or answers yes/no about a piece of text, on this computer."
MAX_ROWS = 200  # rows returned to the assistant; the output file always has all of them
SAFE_SUFFIXES = ("-decided.csv", "-decided.xlsx")  # the only existing files a tool may overwrite

TOOLS = [
    {"name": "decide",
     "description": ("Pick one option, or answer yes/no, about a piece of text. Runs on this computer; nothing is sent anywhere. "
                     "Give options as short descriptions (about 5 to 15 words each, like {\"billing\": \"Billing: charges, refunds\"}). "
                     "Leave options out for a yes/no question."),
     "inputSchema": {"type": "object", "properties": {
         "state": {"type": ["string", "object"], "description": "The text, or an object of fields, to decide on."},
         "question": {"type": "string", "description": "What to decide, as one sentence."},
         "options": {"type": ["array", "object"], "items": {"type": "string"}, "additionalProperties": {"type": "string"},
                     "description": "Optional. A list of option strings, or an object of key to short description. Omit for yes/no."}},
         "required": ["state", "question"]}},
    {"name": "run_recipe",
     "description": ("Run a saved recipe (a set of questions) on every row of a .csv or .xlsx file, a folder of .txt/.md files, or rows "
                     "you pass in. Use list_recipes to see the names. Returns the answers and which rows need a person to review. "
                     "Give exactly one of input_path or rows."),
     "inputSchema": {"type": "object", "properties": {
         "recipe": {"type": ["string", "object"], "description": "A recipe name, or a recipe object."},
         "input_path": {"type": "string", "description": "Path to a .csv, an .xlsx or a folder of .txt/.md files."},
         "rows": {"type": "array", "items": {"type": "object"}, "description": "Rows to decide on, as objects of column to value."},
         "output_path": {"type": "string", "description": ("Optional. Write all result rows to this .csv or .xlsx. "
                                                           "An existing file is only replaced if its name ends with -decided.csv or -decided.xlsx.")}},
         "required": ["recipe"]}},
    {"name": "list_recipes", "description": "List the saved and built-in recipes.", "inputSchema": {"type": "object", "properties": {}}},
]


def _fail(msg):
    return {"content": [{"type": "text", "text": msg}], "structuredContent": {"error": msg}, "isError": True}


def _ok(summary, result):
    result = json.loads(json.dumps(result, default=str))  # e.g. dates from an .xlsx
    return {"content": [{"type": "text", "text": f"{summary}\n{json.dumps(result, separators=(',', ':'))}"}],
            "structuredContent": result, "isError": False}


def _path(p, what):
    if not isinstance(p, str) or not p.strip():
        raise ValueError(f"{what} must be a non-empty string")
    return os.path.abspath(os.path.expanduser(p))


def _check_output(p):
    p = _path(p, "output_path")
    if not p.lower().endswith((".csv", ".xlsx")):
        raise ValueError("output_path must end with .csv or .xlsx")
    if not os.path.isdir(os.path.dirname(p)):
        raise ValueError(f"folder {os.path.dirname(p)!r} does not exist")
    if os.path.lexists(p) and (os.path.islink(p) or not p.lower().endswith(SAFE_SUFFIXES)):
        raise ValueError(f"{p!r} already exists and will not be overwritten; use a new name, or one ending in -decided.csv")
    return p


class Server:
    def __init__(self, model_factory):
        self.factory, self.model = model_factory, None

    def get_model(self):
        if self.model is None:
            self.model = self.factory()  # a failed load is not cached, so the next call retries
        return self.model

    def decide(self, a):
        if "state" not in a or not isinstance(a["state"], (str, dict)):
            raise ValueError("state is required: a string or an object")
        if not isinstance(a.get("question"), str) or not a["question"].strip():
            raise ValueError("question is required: a non-empty string")
        opts = a.get("options")
        if opts is not None and not (isinstance(opts, dict) or (isinstance(opts, list) and all(isinstance(o, str) for o in opts))):
            raise ValueError("options must be a list of strings or an object of key to description")
        m, t = self.get_model(), time.perf_counter()
        if opts:
            out = m.choose(a["state"], a["question"], opts)
            summary = f"{out['choice']} (confidence {out['confidence']:.2f})"
        else:
            p = m.yes_no(a["state"], a["question"])
            out = {"answer": "yes" if p >= 0.5 else "no", "p_yes": p}
            summary = f"{out['answer']} (P(yes) {p:.2f})"
        out["ms"] = round((time.perf_counter() - t) * 1000, 1)
        return _ok(summary, out)

    def run_recipe(self, a):
        r = a.get("recipe")
        if isinstance(r, dict):
            recipe = Recipe.from_dict(r)
        elif isinstance(r, str) and r:
            recipe = Recipe.load(r)
        else:
            raise ValueError("recipe is required: a name or a recipe object")
        if ("input_path" in a) == ("rows" in a):
            raise ValueError("give exactly one of input_path or rows")
        out_path = _check_output(a["output_path"]) if a.get("output_path") is not None else None
        if "input_path" in a:
            src = _path(a["input_path"], "input_path")
            if not os.path.exists(src):
                raise ValueError(f"{src!r} does not exist")
            cols, rows = read_rows(src)
        else:
            rows = a["rows"]
            if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
                raise ValueError("rows must be an array of objects")
            cols = list(dict.fromkeys(k for x in rows for k in x))
        t = time.perf_counter()
        results = recipe.run(rows, model=self.get_model()) if rows else []
        res = {"count": len(results), "needs_review": sum(bool(x.get("needs_review")) for x in results),
               "ms": round((time.perf_counter() - t) * 1000, 1)}
        if out_path:
            (write_xlsx if out_path.lower().endswith(".xlsx") else write_csv)(out_path, recipe.output_columns(cols), results)
            res["output_path"] = out_path
        res.update(rows=results[:MAX_ROWS], truncated=len(results) > MAX_ROWS)
        return _ok(f"{res['count']} rows, {res['needs_review']} need review" + (f", saved to {out_path}" if out_path else ""), res)

    def list_recipes(self, a):
        found = list_recipes()
        return _ok(f"{len(found)} recipes: " + ", ".join(r["name"] for r in found), {"recipes": found})

    def call(self, params):
        name, args = params.get("name"), params.get("arguments") or {}
        fn = {"decide": self.decide, "run_recipe": self.run_recipe, "list_recipes": self.list_recipes}.get(name)
        if fn is None:
            return _fail(f"unknown tool {name!r}; use decide, run_recipe or list_recipes")
        if not isinstance(args, dict):
            return _fail("arguments must be an object")
        try:
            return fn(args)
        except (Exception, SystemExit) as e:  # one line for the assistant, the full trace on stderr
            print(traceback.format_exc(), file=sys.stderr)
            msg = (str(e.code) if isinstance(e, SystemExit) else str(e)).strip().splitlines()
            msg = msg[0] if msg else ""
            return _fail(msg if isinstance(e, (ValueError, RuntimeError, SystemExit)) else f"{type(e).__name__}: {msg}")

    def handle(self, method, params):
        """Result for a request; raises KeyError for an unknown method."""
        if method == "initialize":
            want = params.get("protocolVersion")
            return {"protocolVersion": want if want in VERSIONS else VERSIONS[0], "capabilities": {"tools": {}},
                    "serverInfo": {"name": "decisiontune", "version": __version__}, "instructions": INSTRUCTIONS}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self.call(params)
        raise KeyError(method)


def _err(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def run_stdio(model_factory, stdin=sys.stdin, stdout=sys.stdout):
    srv, out = Server(model_factory), stdout
    if stdin is sys.stdin and hasattr(stdin, "reconfigure"):
        stdin.reconfigure(encoding="utf-8")
    if out is sys.stdout and hasattr(out, "reconfigure"):
        out.reconfigure(encoding="utf-8")

    def send(obj):
        out.write(json.dumps(obj) + "\n")
        out.flush()

    with contextlib.redirect_stdout(sys.stderr):  # a stray print (a download, a library) must not corrupt the protocol
        for line in stdin:
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                send(_err(None, -32700, "parse error: not valid JSON"))
                continue
            if not isinstance(msg, dict):
                send(_err(None, -32600, "invalid request: expected a JSON object"))
                continue
            if "method" not in msg:  # a response to us (we send no requests): ignore
                if "id" in msg and "result" not in msg and "error" not in msg:
                    send(_err(msg["id"], -32600, "invalid request: no method"))
                continue
            if "id" not in msg:  # a notification gets no answer
                continue
            id_, method, params = msg["id"], msg["method"], msg.get("params", {})
            if not isinstance(method, str) or not isinstance(params, dict):
                send(_err(id_, -32600 if not isinstance(method, str) else -32602, "invalid request: method must be a string, params an object"))
                continue
            try:
                send({"jsonrpc": "2.0", "id": id_, "result": srv.handle(method, params)})
            except KeyError:
                send(_err(id_, -32601, f"method not found: {method}"))
            except Exception as e:
                print(traceback.format_exc(), file=sys.stderr)
                send(_err(id_, -32603, f"internal error: {type(e).__name__}"))
