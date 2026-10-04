import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import __version__
from .engine import BACKENDS
from .hub import REPO, DecisionModel, resolve


def decide(m, state, question, options):
    if options:
        return m.choose(state, question, options)
    p = m.yes_no(state, question)
    return {"answer": "yes" if p >= 0.5 else "no", "p_yes": p}


def serve(m, host, port):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send(200, {"model": "DecisionTune 1.0", "backend": m.backend_name, "usage": "POST /decide {state, question, options?}"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if self.path not in ("/", "/decide") or n > 1 << 20:
                return self._send(404 if n <= 1 << 20 else 413, {"error": "POST /decide with a JSON body under 1 MB"})
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
                t = time.perf_counter()
                out = decide(m, req.get("state", ""), req["question"], req.get("options"))
                out["ms"] = round((time.perf_counter() - t) * 1000, 1)
            except (KeyError, ValueError, TypeError, AttributeError) as e:
                return self._send(400, {"error": f"{type(e).__name__}: {e}"})
            self._send(200, out)

    print(f"DecisionTune 1.0 ({m.backend_name}) on http://{host}:{port}/decide")
    HTTPServer((host, port), Handler).serve_forever()  # ponytail: one request at a time; put a real server in front for concurrency


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
    s = sub.add_parser("serve", help="local HTTP JSON endpoint: POST /decide")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--device", default=None)
    common(s)
    args = ap.parse_args(argv)

    if args.cmd == "download":
        print(resolve(args.model, args.backend, args.yes))
        return
    m = DecisionModel.from_pretrained(args.model, backend=args.backend, device=args.device, yes=args.yes)
    if args.cmd == "serve":
        return serve(m, args.host, args.port)
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
