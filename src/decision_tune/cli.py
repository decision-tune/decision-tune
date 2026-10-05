import argparse
import errno
import json
import os
import sys
import time
from pathlib import Path

from . import __version__, mcp, server
from .engine import BACKENDS, FILES, pick_backend
from .hub import REPO, DecisionModel, resolve
from .recipe import BUILTIN, NAME_RE, Recipe, list_recipes, read_rows, recipes_dir, write_csv, write_xlsx
from .server import decide


def _load(args):
    return DecisionModel.from_pretrained(args.model, backend=args.backend, device=args.device, yes=args.yes)


def _cached(model, backend):
    """True if the model is a local directory or already in the Hugging Face cache (never downloads, never prompts)."""
    if os.path.isdir(os.path.expanduser(model)):
        return True
    from huggingface_hub import snapshot_download

    want = ["manifest.json", *FILES[pick_backend(backend)]]
    try:
        return all(os.path.isfile(os.path.join(snapshot_download(model, allow_patterns=want, local_files_only=True), f)) for f in want)
    except Exception:
        return False


def _mcp_model(args):
    yes = args.yes or os.environ.get("DECISION_TUNE_YES") == "1"
    if not yes and not _cached(args.model, args.backend):
        raise RuntimeError("Run `decisiontune download` first.")  # a prompt would read the protocol's stdin
    return DecisionModel.from_pretrained(args.model, backend=args.backend, yes=True)


def _app(args):
    m = _load(args)  # load first: the page the browser opens must be able to answer
    for port in range(args.port, args.port + 11):
        try:
            if port != args.port:
                print(f"decision-tune: port {args.port} is busy, using {port}", file=sys.stderr)
            return server.serve(m, "127.0.0.1", port, False, open_browser=not args.no_browser)  # opens the page once the port is bound
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
    raise RuntimeError(f"ports {args.port} to {args.port + 10} are all busy: pass --port")


def _run(args):
    inp = Path(os.path.expanduser(args.input))
    if not inp.exists():
        raise ValueError(f"no such input: {args.input}")
    out = Path(os.path.expanduser(args.output)) if args.output else inp.parent / f"{inp.resolve().name if inp.is_dir() else inp.stem}-decided.csv"
    if out.exists() and not args.force:
        raise ValueError(f"{out} already exists: pass --force to replace it, or -o for another name")
    recipe = Recipe.load(args.recipe)
    cols, rows = read_rows(inp)
    missing = [c for c in recipe.read if c not in cols]
    if missing:  # otherwise every row would be decided on empty text
        raise ValueError(f"the input has no column {', '.join(map(repr, missing))} (columns: {', '.join(cols) or 'none'})")
    m = _load(args)
    step = lambda i, n: n > 50 and (i % 10 == 0 or i == n) and print(f"\r{i}/{n} rows", end="\n" if i == n else "", file=sys.stderr, flush=True)
    t = time.perf_counter()
    res = recipe.run(rows, model=m, progress=step)
    ms = (time.perf_counter() - t) * 1000
    tmp = out.with_name(f".{out.name}.tmp")  # a failed write must not truncate an existing file
    try:
        (write_xlsx if out.suffix.lower() == ".xlsx" else write_csv)(tmp, recipe.output_columns(cols), res)
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    failed = sum(1 for r in res if "error" in r)
    if failed:
        print(f"decision-tune: {failed} rows failed (see the error column)", file=sys.stderr)
    print(f"{len(res)} rows, {sum(1 for r in res if r.get('needs_review'))} need review, {ms:.0f} ms. Wrote {out}")


def _recipe(args):
    if args.action == "show":
        return print(json.dumps(Recipe.load(args.name).to_dict(), indent=2, ensure_ascii=False))
    if not NAME_RE.fullmatch(args.name):
        raise ValueError(f"recipe name {args.name!r} must match {NAME_RE.pattern}")
    path = Path(recipes_dir(), f"{args.name}.json")
    if path.exists():
        raise ValueError(f"{path} already exists")
    r = Recipe.load(BUILTIN / "support-triage.json")
    r.name = args.name
    print(r.save())




def main(argv=None):
    try:
        return _main(argv)
    except (RuntimeError, ValueError) as e:  # download refused, hash mismatch, bad input: one line, no traceback
        raise SystemExit(f"decision-tune: {e}")


def _main(argv=None):
    ap = argparse.ArgumentParser(prog="decision-tune", description="DecisionTune 1.0: pick an option, or get P(yes), with one encoder pass")
    ap.add_argument("--version", action="version", version=f"decision-tune {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--model", default=REPO, help=f"Hugging Face repo id or local directory (default {REPO})")
        p.add_argument("--backend", default="auto", choices=["auto", *BACKENDS], help="auto = mlx on Apple silicon if installed, else torch")
        p.add_argument("--yes", action="store_true", help="download without asking")

    d = sub.add_parser("download", help="download and verify the model files")
    common(d)
    a = sub.add_parser("ask", help="choose among options, or answer a yes/no question")
    a.add_argument("question")
    a.add_argument("--state", default="", help="the context to decide on")
    a.add_argument("--option", action="append", default=[], help="repeat for each option; omit for a yes/no question")
    a.add_argument("--device", default=None, help="torch device: cpu, cuda or mps (default cuda if available, else cpu)")
    a.add_argument("--json", action="store_true")
    common(a)
    s = sub.add_parser("serve", help="local HTTP JSON endpoint: POST /decide (and the app)")
    s.add_argument("--host", default=None, help="address to listen on (default 127.0.0.1, this computer only)")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--device", default=None)
    common(s)
    p = sub.add_parser("app", help="open the DecisionTune app in your browser")
    p.add_argument("--port", type=int, default=8000, help="first port to try (default 8000; the next 10 are tried if busy)")
    p.add_argument("--no-browser", action="store_true", help="do not open the browser")
    p.add_argument("--device", default=None)
    common(p)
    r = sub.add_parser("run", help="run a recipe on every row of a .csv or .xlsx file, or a folder of .txt/.md files")
    r.add_argument("recipe", help="a recipe name (see `recipes`) or a .json file")
    r.add_argument("input", help=".csv, .xlsx or folder")
    r.add_argument("-o", "--output", help="result file, .csv or .xlsx (default: <input name>-decided.csv next to the input)")
    r.add_argument("--force", action="store_true", help="replace the output file if it exists")
    r.add_argument("--device", default=None)
    common(r)
    sub.add_parser("recipes", help="list the recipes")
    rc = sub.add_parser("recipe", help="show a recipe, or start a new one")
    rcs = rc.add_subparsers(dest="action", required=True)
    rcs.add_parser("show", help="print a recipe as JSON").add_argument("name")
    rcs.add_parser("new", help="save a copy of support-triage under a new name, to edit").add_argument("name")
    mp = sub.add_parser("mcp", help="MCP server over stdio, for AI assistants")
    common(mp)
    args = ap.parse_args(argv)

    if args.cmd == "download":
        print(resolve(args.model, args.backend, args.yes))
        return
    if args.cmd == "app":
        return _app(args)
    if args.cmd == "run":
        return _run(args)
    if args.cmd == "recipes":
        for r in list_recipes():
            print(f"{r['name']:<24}{r['source']:<10}{', '.join(r['questions'])}")
        return
    if args.cmd == "recipe":
        return _recipe(args)
    if args.cmd == "mcp":
        return mcp.run_stdio(lambda: _mcp_model(args))
    m = _load(args)
    if args.cmd == "serve":
        return server.serve(m, args.host or "127.0.0.1", args.port, explicit_host=args.host is not None)
    out = decide(m, args.state, args.question, args.option)
    if args.json:
        print(json.dumps(out, indent=2))
    elif "p_yes" in out:
        print(f"{out['answer']}  (P(yes) = {out['p_yes']:.4f})")
    else:
        print(f"{out['choice']}  (confidence {out['confidence']:.3f})")
        for k, v in sorted(out["probabilities"].items(), key=lambda kv: -kv[1]):
            print(f"  {v:.3f}  {k}")


if __name__ == "__main__":
    main()
