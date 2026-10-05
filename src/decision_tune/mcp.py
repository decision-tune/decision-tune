"""MCP server over stdio for Claude Desktop, Cursor and any MCP client: newline-delimited JSON-RPC 2.0, stdlib only.

    run_stdio(DecisionModel.from_pretrained)

stdout carries protocol messages only; everything else goes to stderr. The model loads on the first tool call that needs it.
"""
import contextlib
import json
import os
import stat
import sys
import tempfile
import time
import traceback

from . import __version__
from .recipe import Recipe, check_options, list_recipes, read_rows, write_csv, write_xlsx

VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = "DecisionTune picks an option or answers yes/no about a piece of text, on this computer."
REPLY_ROWS = 200  # rows returned to the assistant; the output file always has all of them
MAX_LINE_BYTES = 30 * 1024 * 1024  # one JSON-RPC line
MAX_INPUT_BYTES = 200 * 1024 * 1024  # one input file
MAX_ROWS = 100_000  # rows in one run
MAX_DECISIONS = 2_000_000  # rows x questions in one run
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
         "required": ["state", "question"]},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
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
         "required": ["recipe"]},
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}},
    {"name": "list_recipes", "description": "List the saved and built-in recipes.", "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"readOnlyHint": True, "openWorldHint": False}},
]


def _fail(msg):
    return {"content": [{"type": "text", "text": msg}], "structuredContent": {"error": msg}, "isError": True}


def _ok(summary, result):
    result = json.loads(json.dumps(result, default=str, allow_nan=False))  # e.g. dates from an .xlsx
    return {"content": [{"type": "text", "text": f"{summary}\n{json.dumps(result, separators=(',', ':'), allow_nan=False)}"}],
            "structuredContent": result, "isError": False}


def _path(p, what):
    if not isinstance(p, str) or not p.strip():
        raise ValueError(f"{what} must be a non-empty string")
    return os.path.abspath(os.path.expanduser(p))


def resolve_roots(roots):
    """Real paths of the allowed folders; a folder that does not exist is an error."""
    out = []
    for r in roots:
        p = os.path.realpath(os.path.expanduser(r))
        if not os.path.isdir(p):
            raise ValueError(f"--allow folder does not exist: {r}")
        out.append(p)
    return out


def _inside(p, roots):
    p = os.path.realpath(p)  # follows symlinks to the real target
    return any(os.path.commonpath([p, r]) == r for r in roots)  # commonpath, so /a/b does not admit /a/bc


def _check_output(p):
    p = _path(p, "output_path")
    if not p.lower().endswith((".csv", ".xlsx")):
        raise ValueError("output_path must end with .csv or .xlsx")
    if not os.path.isdir(os.path.dirname(p)):
        raise ValueError(f"folder {os.path.dirname(p)!r} does not exist")
    _refuse_clobber(p)
    return p


def _refuse_clobber(p):
    if os.path.lexists(p) and (os.path.islink(p) or not p.lower().endswith(SAFE_SUFFIXES)):
        raise ValueError(f"{p!r} already exists and will not be overwritten; use a new name, or one ending in -decided.csv")


def _input_file(p):
    try:
        st = os.stat(p)
    except OSError as e:
        raise ValueError(f"{p!r} cannot be read: {e.strerror or type(e).__name__}") from None
    if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode)):  # a FIFO or device would block the server
        raise ValueError(f"{p!r} is not a file or a folder")
    if stat.S_ISREG(st.st_mode) and st.st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{p!r} is larger than {MAX_INPUT_BYTES // 2**20} MB")


def _write_output(p, write, columns, rows):
    """Write to a new temp file beside p, then swap it in: the destination inode is never opened, so a hard link keeps its content."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".decisiontune-", suffix=os.path.splitext(p)[1])
    try:
        umask = os.umask(0)
        os.umask(umask)
        os.fchmod(fd, 0o666 & ~umask)
        os.close(fd)
        write(tmp, columns, rows)
        _refuse_clobber(p)  # again: the run may have taken minutes
        if p.lower().endswith(SAFE_SUFFIXES):
            os.replace(tmp, p)
            return
        try:
            os.link(tmp, p)  # exclusive: a file that appeared meanwhile makes this fail instead of being overwritten
        except FileExistsError:
            raise ValueError(f"{p!r} already exists and will not be overwritten; use a new name, or one ending in -decided.csv") from None
        except OSError:  # a filesystem without hard links; the check above is the guard
            os.replace(tmp, p)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


class Server:
    def __init__(self, model_factory, roots=None):
        self.factory, self.model, self.roots = model_factory, None, roots

    def allow(self, p):
        """Refuse a path outside the allowed folders; with no folders set, everything is allowed."""
        if self.roots and not _inside(p, self.roots):
            raise ValueError(f"Path is outside the allowed folders: {p}. Add it with --allow.")

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
        if opts is not None and opts != [] and opts != {}:  # no options: a yes or no question
            check_options(opts)
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
            if r.endswith(".json"):  # a path; recipe names stay allowed
                self.allow(_path(r, "recipe"))
            recipe = Recipe.load(r)
        else:
            raise ValueError("recipe is required: a name or a recipe object")
        if ("input_path" in a) == ("rows" in a):
            raise ValueError("give exactly one of input_path or rows")
        out_path = _check_output(a["output_path"]) if a.get("output_path") is not None else None
        if out_path:
            self.allow(os.path.dirname(out_path))
            self.allow(out_path)
        if "input_path" in a:
            src = _path(a["input_path"], "input_path")
            self.allow(src)
            if self.roots and os.path.isdir(src):  # each entry may be a symlink out of the folder
                for f in os.scandir(src):
                    if f.name.lower().endswith((".txt", ".md")):
                        self.allow(f.path)
            _input_file(src)
            cols, rows = read_rows(src)
        else:
            rows = a["rows"]
            if not isinstance(rows, list) or not all(isinstance(x, dict) for x in rows):
                raise ValueError("rows must be an array of objects")
            cols = list(dict.fromkeys(k for x in rows for k in x))
        recipe.check_columns(cols)
        if len(rows) > MAX_ROWS or len(rows) * len(recipe.questions) > MAX_DECISIONS:
            raise ValueError(f"too much work in one run: at most {MAX_ROWS} rows and {MAX_DECISIONS} decisions; split the input")
        t = time.perf_counter()
        results = recipe.run(rows, model=self.get_model()) if rows else []
        res = {"count": len(results), "needs_review": sum(bool(x.get("needs_review")) for x in results),
               "ms": round((time.perf_counter() - t) * 1000, 1)}
        if out_path:
            _write_output(out_path, write_xlsx if out_path.lower().endswith(".xlsx") else write_csv, recipe.output_columns(cols), results)
            res["output_path"] = out_path
        res.update(rows=results[:REPLY_ROWS], truncated=len(results) > REPLY_ROWS)
        return _ok(f"{res['count']} rows, {res['needs_review']} need review" + (f", saved to {out_path}" if out_path else ""), res)

    def list_recipes(self, a):
        found = list_recipes()
        return _ok(f"{len(found)} recipes: " + ", ".join(r["name"] for r in found), {"recipes": found})

    def call(self, params):
        name, args = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str):
            return _fail("name must be a string: decide, run_recipe or list_recipes")
        fn = {"decide": self.decide, "run_recipe": self.run_recipe, "list_recipes": self.list_recipes}.get(name)
        if fn is None:
            return _fail(f"unknown tool {name!r}; use decide, run_recipe or list_recipes")
        if args is None:
            args = {}
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


def _bad_id(v):
    return isinstance(v, bool) or not isinstance(v, (str, int))


def _no_constant(c):
    raise ValueError(f"{c} is not valid JSON")


def run_stdio(model_factory, stdin=sys.stdin, stdout=sys.stdout, roots=None):
    roots = resolve_roots(roots) if roots else None
    if not roots:
        print("decisiontune mcp: no --allow folders set; tools can read any .csv, .xlsx, .txt or .md file you can read.", file=sys.stderr)
    srv, out, saved = Server(model_factory, roots), stdout, None
    if stdin is sys.stdin and hasattr(stdin, "reconfigure"):
        stdin.reconfigure(encoding="utf-8")
    if out is sys.stdout:
        try:
            if out.fileno() == 1:  # protocol on a private copy of fd 1; native writes to fd 1 now land on stderr
                out.flush()
                saved = os.dup(1)
                os.dup2(2, 1)
                out = os.fdopen(saved, "w", encoding="utf-8", newline="\n", closefd=False)
        except (AttributeError, OSError, ValueError):  # not a real stdout (captured, closed): keep it as is
            pass

    def send(obj):
        try:
            line = json.dumps(obj, allow_nan=False)
        except (ValueError, RecursionError):
            line = json.dumps(_err(obj.get("id"), -32603, "internal error: response is not valid JSON"))
        out.write(line + "\n")
        out.flush()

    def drain():  # skip the rest of an oversized line
        while (rest := stdin.readline(65536)) and not rest.endswith("\n"):
            pass

    try:
        with contextlib.redirect_stdout(sys.stderr):  # a stray print (a download, a library) must not corrupt the protocol
            while line := stdin.readline(MAX_LINE_BYTES + 1):
                body = line.rstrip("\r\n")
                if len(body) > MAX_LINE_BYTES or len(body.encode("utf-8", "replace")) > MAX_LINE_BYTES:
                    if not line.endswith("\n"):
                        drain()
                    send(_err(None, -32600, f"invalid request: line is longer than {MAX_LINE_BYTES} bytes"))
                    continue
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line, parse_constant=_no_constant)
                except Exception:  # ValueError, or RecursionError on deep nesting
                    send(_err(None, -32700, "parse error: not valid JSON"))
                    continue
                if not isinstance(msg, dict):
                    send(_err(None, -32600, "invalid request: expected a JSON object"))
                    continue
                if "method" not in msg:  # a response to us (we send no requests): ignore
                    if "id" in msg and "result" not in msg and "error" not in msg:
                        send(_err(None if _bad_id(msg["id"]) else msg["id"], -32600, "invalid request: no method"))
                    continue
                id_ = msg.get("id")
                if msg.get("jsonrpc") != "2.0" or "id" in msg and _bad_id(id_):
                    send(_err(None if _bad_id(id_) else id_, -32600, 'invalid request: "jsonrpc" must be "2.0" and id a string or an integer'))
                    continue
                if "id" not in msg:  # a notification gets no answer
                    continue
                method, params = msg["method"], msg.get("params", {})
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
    finally:
        if saved is not None:
            out.flush()
            os.dup2(saved, 1)  # give the caller its stdout back
            os.close(saved)
