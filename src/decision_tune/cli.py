import argparse
import errno
import ipaddress
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

from . import __version__, mcp, server
from .engine import BACKENDS
from .hub import REPO, DecisionModel, resolve
from .recipe import BUILTIN, NAME_RE, Recipe, check_options, list_recipes, read_rows, recipes_dir, write_csv, write_xlsx
from .server import decide


def _load(args):
    return DecisionModel.from_pretrained(args.model, backend=args.backend, device=args.device, yes=args.yes)


def _mcp_model(args):
    yes = (args.yes or os.environ.get("DECISION_TUNE_YES") == "1") or None  # None: the loader downloads only if cached or consented
    try:
        return DecisionModel.from_pretrained(args.model, backend=args.backend, yes=yes)
    except RuntimeError as e:
        if "not downloaded yet" in str(e):  # hub._confirm refused: stdin is the protocol, there is nobody to ask
            raise RuntimeError("Run `decisiontune download` first.") from e
        raise


def _app(args):
    m = _load(args)  # load first: the page the browser opens must be able to answer
    for port in range(args.port, args.port + 11):
        try:
            return server.serve(m, "127.0.0.1", port, False, open_browser=not args.no_browser)  # prints the bound URL, then opens the page
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
    raise RuntimeError(f"ports {args.port} to {args.port + 10} are all busy: pass --port")


def _run(args):
    inp = Path(os.path.expanduser(args.input))
    if not inp.exists():
        raise ValueError(f"no such input: {args.input}")
    base = inp.resolve() if inp.is_dir() else inp  # "." and ".." have no name of their own
    out = Path(os.path.expanduser(args.output)) if args.output else base.parent / f"{base.name if inp.is_dir() else base.stem}-decided.csv"
    exists = ValueError(f"{out} already exists: pass --force to replace it, or -o for another name")
    if os.path.lexists(out) and not args.force:  # lexists: a dangling symlink counts
        raise exists
    recipe = Recipe.load(args.recipe)
    cols, rows = read_rows(inp)
    recipe.check_columns(cols)  # otherwise every row would be decided on empty text
    m = _load(args)
    step = lambda i, n: n > 50 and (i % 10 == 0 or i == n) and print(f"\r{i}/{n} rows", end="\n" if i == n else "", file=sys.stderr, flush=True)
    t = time.perf_counter()
    failed = []
    res = recipe.run(rows, model=m, progress=step, failed=failed)
    ms = (time.perf_counter() - t) * 1000
    fd, name = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.", suffix=".tmp")  # exclusive and unique: never a file that was already there
    os.close(fd)
    tmp = Path(name)
    try:
        (write_xlsx if out.suffix.lower() == ".xlsx" else write_csv)(tmp, recipe.output_columns(cols), res)
        mask = os.umask(0)
        os.umask(mask)
        os.chmod(tmp, 0o666 & ~mask)  # mkstemp makes it 0600; a result file gets the usual permissions
        if args.force:
            os.replace(tmp, out)
        else:
            try:
                os.link(tmp, out)  # fails atomically if out appeared since the check, dangling symlink included
            except FileExistsError:
                raise exists
            except OSError:  # no hard links on this filesystem: best effort, a narrow race remains
                if os.path.lexists(out):
                    raise exists
                os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    if failed:
        print(f"decision-tune: {len(failed)} rows failed (see the error column)", file=sys.stderr)
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


def _loopback(host):
    """True when the host is unset or every address it names is loopback (127.1, LOCALHOST, ::1 ...)."""
    if host is None:
        return True
    try:
        addrs = {i[4][0] for i in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_loopback for a in addrs)


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
    p.add_argument("--port", type=int, default=8000, help="first port to try (default 8000); tries the next 10 ports if it is busy")
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
    mp.add_argument("--allow", action="append", default=[], metavar="DIR",
                    help="only read and write inside this folder (repeatable; also DECISION_TUNE_ROOTS)")
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
        try:
            roots = mcp.resolve_roots([r for r in os.environ.get("DECISION_TUNE_ROOTS", "").split(os.pathsep) if r] + args.allow)
        except ValueError as e:
            raise SystemExit(str(e)) from None
        return mcp.run_stdio(lambda: _mcp_model(args), roots=roots or None)
    if args.cmd == "ask" and args.option:
        check_options(args.option)  # before the model loads
    m = _load(args)
    if args.cmd == "serve":
        return server.serve(m, args.host or "127.0.0.1", args.port, explicit_host=not _loopback(args.host))
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
