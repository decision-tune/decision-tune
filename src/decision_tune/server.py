"""Local HTTP server: the browser app, the JSON API (/decide, /api/*) and recipe runs. Stdlib only, no network calls.

    serve(DecisionModel.from_pretrained(), port=8000, open_browser=True)
"""
import base64
import binascii
import csv
import io
import json
import re
import socket
import threading
import time
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .recipe import (NAME_RE, Recipe, _rows, list_recipes, recipes_dir, rows_from_csv_text, rows_from_lines, write_csv,
                     write_xlsx)

APP = Path(__file__).parent / "app"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]")
SMALL, BIG = 1 << 20, 25 << 20  # body caps: /decide and recipes, runs and exports
MAX_ROWS, MAX_COLUMNS, MAX_CELLS = 100_000, 1_000, 5_000_000  # input and export; cells = rows x columns
MAX_DECISIONS = 2_000_000  # rows x questions in one run
MAX_ZIP_TOTAL, MAX_ZIP_MEMBER = 200 << 20, 100 << 20  # an .xlsx is a zip: bytes after unpacking
READ_TIMEOUT, MAX_ACTIVE = 30, 16  # seconds per socket read; connections served at once
MSG_FIELDS = "the request is missing a field or a field has the wrong type"
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
SECURITY = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY"}
RUN_RE = re.compile(r"^/recipes/([^/]+)/run$")
RECIPE_RE = re.compile(r"^/api/recipes/([^/]+)$")


class _Limit(Exception):
    """A size limit was hit: the message names it. Sent as 413."""


class _ModelFailed(Exception):
    """The model raised inside /decide: nothing from the original error is shown."""


def _check(columns, rows):
    if len(rows) > MAX_ROWS:
        raise _Limit(f"too many rows: the limit is {MAX_ROWS:,} rows")
    if len(columns) > MAX_COLUMNS:
        raise _Limit(f"too many columns: the limit is {MAX_COLUMNS:,} columns")
    if len(rows) * len(columns) > MAX_CELLS:
        raise _Limit(f"too many cells: the limit is {MAX_CELLS:,} (rows times columns)")


def decide(m, state, question, options):
    if options:
        return m.choose(state, question, options)
    p = m.yes_no(state, question)
    return {"answer": "yes" if p >= 0.5 else "no", "p_yes": p}


class _Locked:
    """Inference is not thread-safe: one model call at a time, whichever request makes it."""

    def __init__(self, model):
        self.model, self.lock = model, threading.Lock()

    def choose(self, state, question, options):
        with self.lock:
            return self.model.choose(state, question, options)

    def yes_no(self, state, question):
        with self.lock:
            return self.model.yes_no(state, question)


def _scalar(v):
    return v if v is None or isinstance(v, (str, int, float, bool)) else json.dumps(v, default=str, ensure_ascii=False)


def _clean_rows(rows):
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("rows must be a list of objects")
    _check((), rows)
    return [{str(k): _scalar(v) for k, v in r.items()} for r in rows]


def _columns(rows):
    return list(dict.fromkeys(k for r in rows for k in r))


def _xlsx_rows(raw):
    """(columns, rows) from the first sheet. The zip is measured before openpyxl sees it; rows and cells are counted as they are read."""
    import openpyxl

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            sizes = [i.file_size for i in z.infolist()]
    except zipfile.BadZipFile:
        raise ValueError("cannot read the Excel file") from None
    if sum(sizes) > MAX_ZIP_TOTAL or max(sizes, default=0) > MAX_ZIP_MEMBER:
        raise _Limit(f"the Excel file is too large once unpacked: the limit is {MAX_ZIP_TOTAL >> 20} MB in total and {MAX_ZIP_MEMBER >> 20} MB for any part")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        try:
            it = wb.worksheets[0].iter_rows(values_only=True)
            cols = [str(c) for c in next(it, ())]
            _check(cols, ())
            records = []
            for n, r in enumerate(it, 1):  # blank rows count: a sheet of empty rows is still work to scan
                _check(cols, range(n))
                if any(v is not None for v in r):
                    records.append(dict(zip(cols, r)))
            return _rows(cols, records)
        finally:
            wb.close()
    except _Limit:
        raise
    except Exception:  # bad sheet, missing sheet: one fixed message, nothing from the file
        raise ValueError("cannot read the Excel file") from None


def _read_input(inp):
    if not isinstance(inp, dict) or "data" not in inp:
        raise ValueError("input must be an object with a type and data")
    kind, data = inp.get("type"), inp["data"]
    if kind == "rows":
        rows = _clean_rows(data)
        cols = _columns(rows)
    elif not isinstance(data, str):
        raise ValueError("input data must be text")
    elif kind == "csv":
        cols, rows = rows_from_csv_text(data)
    elif kind == "lines":
        cols, rows = rows_from_lines(data)
    elif kind == "xlsx":
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("xlsx data must be base64") from None
        cols, rows = _xlsx_rows(raw)
    else:
        raise ValueError("input type must be csv, xlsx, lines or rows")
    _check(cols, rows)
    return cols, rows


def _is_text(v):
    if not isinstance(v, str) or not v.strip():
        return False
    try:
        float(v)
        return False
    except ValueError:
        return True


def _preview(columns, rows):
    """What the app needs to draw step 2: the columns, the row count and which columns hold text."""
    return {"columns": columns, "count": len(rows),
            "text": [c for c in columns if any(_is_text(r.get(c)) for r in rows[:200])]}


def _recipe(spec):
    if isinstance(spec, str):
        if not NAME_RE.fullmatch(spec):  # Recipe.load would also open a path: names only over HTTP
            raise ValueError("recipe must be a saved recipe name or a recipe object")
        return Recipe.load(spec)
    return Recipe.from_dict(spec)


def _run(model, recipe, columns, rows):
    _check(columns, rows)
    if len(rows) * len(recipe.questions) > MAX_DECISIONS:
        raise _Limit(f"too many decisions: the limit is {MAX_DECISIONS:,} (rows times questions)")
    t = time.perf_counter()
    out, failed = [], []
    for i, row in enumerate(rows):  # same as recipe.run, but a failed row is known by position, not by a column name the data may also use
        res = recipe.decide_row(model, row)
        if "error" in res:  # decide_row sets it only on failure, and a question may not be named "error"
            failed.append(i)
        out.append({**row, **res})
    return {"columns": recipe.output_columns(columns), "rows": out, "needs_review": sum(1 for r in out if r.get("needs_review")),
            "count": len(out), "failed": failed, "ms": round((time.perf_counter() - t) * 1000, 1)}


def _export(body):
    cols, rows, fmt = body.get("columns"), body.get("rows"), body.get("format")
    if not isinstance(cols, list) or not all(isinstance(c, str) for c in cols):
        raise ValueError("columns must be a list of names")
    rows = _clean_rows(rows)
    _check(cols, rows)
    if len(set(cols)) != len(cols):  # a repeated name would write the same cell again and again
        raise ValueError("column names must be different")
    if fmt == "csv":
        buf = io.StringIO()
        write_csv(buf, cols, rows)
        return "\ufeff".encode("utf-8") + buf.getvalue().encode("utf-8"), "text/csv; charset=utf-8", "decisions.csv"
    if fmt == "xlsx":
        buf = io.BytesIO()
        write_xlsx(buf, cols, rows)
        return buf.getvalue(), XLSX, "decisions.xlsx"
    raise ValueError("format must be csv or xlsx")


def _decide_args(req):
    """Check a /decide body before any model call. Returns (state, question, options or None)."""
    if not isinstance(req, dict):
        raise ValueError("send a JSON object with state, question and options")
    state, question, opts = req.get("state", ""), req.get("question"), req.get("options")
    if not isinstance(state, (str, dict)):
        raise ValueError("state must be text")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be non-empty text")
    if opts is None or opts == [] or opts == {}:  # no options: a yes or no question
        return state, question, None
    if isinstance(opts, dict):
        ok = all(isinstance(k, str) and k and isinstance(d, str) for k, d in opts.items())
    else:
        ok = isinstance(opts, list) and all(isinstance(o, str) and o for o in opts) and len(set(opts)) == len(opts)
    if not ok or not 2 <= len(opts) <= 32:
        raise ValueError("options must be 2 to 32 different, non-empty texts (a list or a {key: description} object)")
    return state, question, opts


class Handler(BaseHTTPRequestHandler):
    server_version = "DecisionTune"
    sys_version = ""
    timeout = READ_TIMEOUT  # a client that stops sending is cut off after this many seconds

    def log_message(self, *a):  # nothing from a request ever reaches the console
        pass

    def send_error(self, code, message=None, explain=None):
        """Protocol errors (bad request line, too long, unknown method) get the same JSON and headers as everything else."""
        self.close_connection = True
        if code == 501 and getattr(self, "headers", None) is not None:  # a method with no do_ handler: same checks as PUT
            return self._other()
        try:
            phrase = HTTPStatus(code).phrase
        except ValueError:
            phrase = "error"
        self._err(code, phrase)

    # ---- responses
    def _send(self, code, body, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in {**SECURITY, **(extra or {})}.items():
            self.send_header(k, v)
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, default=str).encode())

    def _err(self, code, msg):
        self._json(code, {"error": msg})

    # ---- checks
    def _allowed(self, post):
        host = self.headers.get("Host", "")
        name = host.rsplit(":", 1)[0] if re.search(r":\d+$", host) else host
        if not self.server.explicit_host and name.lower() not in LOCAL_HOSTS:
            self._err(403, "this host name is not allowed")
            return False
        if not post:
            return True
        origin = self.headers.get("Origin")
        if (origin is not None and origin != f"http://{host}") or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._err(403, "cross-origin requests are not allowed")
            return False
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() not in ("application/json", "text/csv"):
            self._err(415, "send Content-Type application/json or text/csv")
            return False
        return True

    def _body(self, cap):
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._err(411, "send a Content-Length header")
            return None
        if n < 0 or n > cap:
            self._err(413, f"the body must be under {cap >> 20} MB")
            return None
        try:
            return self.rfile.read(n)
        except TimeoutError:
            self.close_connection = True
            self._err(408, f"the request body did not arrive within {self.timeout} seconds")
            return None

    # ---- routes
    def do_GET(self):
        if not self._allowed(False):
            return
        try:
            self._get(self.path.split("?", 1)[0])
        except Exception:  # e.g. an unreadable recipe file: a fixed answer, never a closed connection
            self._err(500, "this computer could not read that")

    def _get(self, path):
        if path == "/api/status":
            m = self.server.model
            return self._json(200, {"model": "DecisionTune 1.0", "backend": getattr(m, "backend_name", "unknown"), "version": __version__})
        if path == "/api/recipes":
            return self._json(200, list_recipes())
        hit = RECIPE_RE.match(path)
        if hit:
            if not NAME_RE.fullmatch(hit[1]):
                return self._err(404, "not found")
            try:
                return self._json(200, Recipe.load(hit[1]).to_dict())
            except ValueError:
                return self._err(404, "no such recipe")
        self._static(path)

    def _static(self, path):
        rel = {"/": "index.html"}.get(path) or path.lstrip("/")
        try:
            f = (APP / rel).resolve()
            if f.suffix in MIME and f.is_file() and f.parent in (APP.resolve(), (APP / "fonts").resolve()) and rel == f.relative_to(APP.resolve()).as_posix():
                return self._send(200, f.read_bytes(), MIME[f.suffix])
        except (ValueError, OSError):  # e.g. a NUL byte in the path
            pass
        self._err(404, "not found")

    def do_POST(self):
        if not self._allowed(True):
            return
        path = self.path.split("?", 1)[0]
        known = path in ("/", "/decide", "/api/recipes", "/api/run", "/api/export", "/api/preview") or RUN_RE.match(path)
        if not known:
            return self._err(404, "not found")
        body = self._body(SMALL if path in ("/", "/decide", "/api/recipes") else BIG)
        if body is None:
            return
        try:
            self._post(path, body)
        except _Limit as e:
            self._err(413, str(e))
        except ImportError:
            self._err(500, "Excel support is not installed (pip install openpyxl)")
        except ValueError as e:  # ours and Recipe's messages are written for people; JSON and UTF-8 errors get fixed text
            msg = "invalid JSON" if isinstance(e, json.JSONDecodeError) else "body must be UTF-8 text" if isinstance(e, UnicodeDecodeError) else str(e)
            self._err(400, msg[:300])
        except csv.Error:
            self._err(400, "cannot read the CSV data")
        except (KeyError, TypeError, AttributeError):  # fixed text: these messages can hold the request
            self._err(400, MSG_FIELDS)
        except OSError:
            self._err(500, "a file on this computer could not be read or written")
        except Exception:  # a model failure: say so, send no traceback
            self._err(500, "the model could not decide this input")

    def _post(self, path, body):
        m = self.server.model
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if path in ("/", "/decide"):
            state, question, options = _decide_args(json.loads(body or b"{}"))
            t = time.perf_counter()
            try:
                out = decide(m, state, question, options)
            except Exception:
                raise _ModelFailed from None
            out["ms"] = round((time.perf_counter() - t) * 1000, 1)
            return self._json(200, out)
        if path == "/api/recipes":
            return self._json(200, {"saved": self._save(Recipe.from_dict(json.loads(body)))})
        if path == "/api/run":
            req = json.loads(body)
            recipe = _recipe(req["recipe"])
            cols, rows = _read_input(req["input"])
            return self._json(200, _run(m, recipe, cols, rows))
        if path == "/api/preview":
            return self._json(200, _preview(*_read_input(json.loads(body)["input"])))
        if path == "/api/export":
            data, ctype, name = _export(json.loads(body))
            return self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{name}"'})
        recipe = _recipe(RUN_RE.match(path)[1])  # /recipes/<name>/run
        if ctype == "text/csv":
            cols, rows = rows_from_csv_text(body.decode("utf-8"))
        else:
            rows = _clean_rows(json.loads(body)["rows"])
            cols = _columns(rows)
        self._json(200, _run(m, recipe, cols, rows))

    @staticmethod
    def _save(recipe):
        recipe.save(recipes_dir())
        return recipe.name

    def _other(self):  # PUT, DELETE, PATCH, OPTIONS, HEAD and unknown methods: nothing uses them, and no CORS preflight ever succeeds
        if self._allowed(self.command not in ("HEAD", "OPTIONS")):  # a method that could change state gets the POST checks
            self._err(405, "method not allowed")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _other


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # a dropped connection must not print a traceback
        pass

    def __init__(self, addr, handler):
        if ":" in addr[0]:
            self.address_family = socket.AF_INET6
        self.slots = threading.BoundedSemaphore(MAX_ACTIVE)
        super().__init__(addr, handler)

    def process_request(self, request, client_address):  # runs in the accept loop: never blocks on a client
        if self.slots.acquire(blocking=False):
            super().process_request(request, client_address)
        else:
            self._busy(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def _busy(self, request):
        body = b'{"error": "too many requests at once: try again in a moment"}'
        head = ("HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nRetry-After: 1\r\n" + f"Content-Length: {len(body)}\r\n"
                + "".join(f"{k}: {v}\r\n" for k, v in SECURITY.items()) + "\r\n")
        try:
            request.settimeout(0.2)
            request.sendall(head.encode() + body)
            request.shutdown(socket.SHUT_WR)
            request.recv(65536)  # take what the client sent, so closing does not reset the connection before it reads the 503
        except OSError:
            pass
        self.shutdown_request(request)


def make_server(model, host="127.0.0.1", port=8000, explicit_host=False):
    s = _Server((host, port), Handler)
    s.model, s.explicit_host = _Locked(model), explicit_host
    s.model.backend_name = getattr(model, "backend_name", "unknown")
    return s


def serve(model, host="127.0.0.1", port=8000, explicit_host=False, open_browser=False):
    s = make_server(model, host, port, explicit_host)
    url = f"http://{'[%s]' % host if ':' in host else host}:{s.server_address[1]}/"
    print(f"DecisionTune 1.0 ({s.model.backend_name}) on {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        s.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        s.server_close()
