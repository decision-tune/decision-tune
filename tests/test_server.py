import base64
import csv
import http.client
import io
import json
import os
import re
import threading
import zipfile

import pytest

from decision_tune.recipe import Recipe, write_xlsx
from decision_tune.server import make_server

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tickets.csv")
CSV_TEXT = open(DATA, encoding="utf-8").read()
J = {"Content-Type": "application/json"}


def _n(state, question):
    return sum(map(ord, str(state) + question))


def fk_choose(state, question, keys):
    """Pure function of (state, question, option keys): the pick and its confidence (0.40 to 0.94)."""
    n = _n(state, question)
    return keys[n % len(keys)], 0.4 + (n % 55) / 100


REFUND_YES = {"Order #4417", "Double charge", "Where is my box"}  # rows 0, 1 and 3 of tickets.csv answer yes, the other five no


def fk_yes(state, question):
    yes = "refund" in str(state) or (isinstance(state, dict) and state.get("subject") in REFUND_YES)
    return (0.95 if yes else 0.1) + (_n(state, question) % 5) / 100


class Fake:
    """Answers are pure functions of the input, so a test can recompute them. Records every call."""

    def __init__(self):
        self.log = []
        self.backend_name = "fake"

    @property
    def calls(self):
        return len(self.log)

    def choose(self, state, question, options):
        self.log.append(("choose", state, question, options))
        keys = list(options)
        pick, conf = fk_choose(state, question, keys)
        return {"choice": pick, "probabilities": {k: 1 / len(keys) for k in keys}, "confidence": conf}

    def yes_no(self, state, question):
        self.log.append(("yes_no", state, question, None))
        return fk_yes(state, question)


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_TUNE_HOME", str(tmp_path))
    model = Fake()
    s = make_server(model, port=0)
    threading.Thread(target=s.serve_forever, args=(0.01,), daemon=True).start()
    s.fake = model
    yield s
    s.shutdown()
    s.server_close()


def call(s, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=10)
    if isinstance(body, (dict, list)):
        body = json.dumps(body)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    out = (r.status, dict(r.getheaders()), data)
    c.close()
    return out


def post(s, path, obj, headers=J):
    st, h, data = call(s, "POST", path, obj, headers)
    return st, h, (json.loads(data) if h["Content-Type"].startswith("application/json") else data)


# 1. static files, status, headers
def test_index_has_csp_and_security_headers(srv):
    st, h, body = call(srv, "GET", "/")
    assert st == 200 and h["Content-Type"].startswith("text/html")
    assert b"DecisionTune" in body
    assert h["Content-Security-Policy"].startswith("default-src 'self'; script-src 'self'; style-src 'self'")
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff" and h["Cache-Control"] == "no-store"
    assert h["Referrer-Policy"] == "no-referrer" and h["X-Frame-Options"] == "DENY"
    assert not any(k.lower().startswith("access-control") for k in h)


def test_static_assets_and_status(srv):
    st, h, body = call(srv, "GET", "/app.js")
    assert st == 200 and "javascript" in h["Content-Type"] and body
    assert call(srv, "GET", "/app.css")[0] == 200
    st, h, body = call(srv, "GET", "/fonts/Geist-var-latin.woff2")
    assert st == 200 and h["Content-Type"] == "font/woff2" and body[:4] == b"wOF2"
    st, _, body = call(srv, "GET", "/api/status")
    out = json.loads(body)
    assert st == 200 and out["model"] == "DecisionTune 1.0" and out["backend"] == "fake" and out["version"]


@pytest.mark.parametrize("path", ["/../server.py", "/fonts/../../server.py", "/%2e%2e/server.py", "/fonts/../app.js",
                                  "/server.py", "/fonts/", "/fonts", "//etc/passwd", "/x%00.js", "/nope"])
def test_traversal_and_unknown_paths_are_404(srv, path):
    st, h, body = call(srv, "GET", path)
    assert st == 404 and json.loads(body)["error"] and b"Traceback" not in body


# 2. /decide keeps the old response shape
def test_decide_choose_and_yes_no(srv):
    for path in ("/decide", "/"):
        st, _, out = post(srv, path, {"state": "box broke", "question": "Which?", "options": ["a", "b"]})
        assert st == 200 and out["choice"] in ("a", "b") and "probabilities" in out and out["ms"] >= 0
    st, _, out = post(srv, "/decide", {"state": "I want a refund", "question": "Refund?"})
    assert st == 200 and out["answer"] == "yes" and out["p_yes"] == fk_yes("I want a refund", "Refund?") and "ms" in out


@pytest.mark.parametrize("body", [{"state": "x"}, [1], "nope"])
def test_decide_bad_input_is_400(srv, body):
    st, _, out = post(srv, "/decide", body)
    assert st == 400 and out["error"]
    assert call(srv, "POST", "/decide", "{not json", J)[0] == 400


def test_decide_body_cap(srv):
    import socket

    sock = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=10)  # declare a big body, send none
    sock.sendall(b"POST /decide HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % ((1 << 20) + 1))
    assert sock.recv(64).startswith(b"HTTP/1.0 413")
    sock.close()


# 3. recipes API
def test_recipe_list_get_and_save(srv, tmp_path):
    st, _, body = call(srv, "GET", "/api/recipes")
    assert st == 200 and "support-triage" in [r["name"] for r in json.loads(body)]
    st, _, body = call(srv, "GET", "/api/recipes/support-triage")
    assert st == 200 and json.loads(body)["name"] == "support-triage"
    for bad in ("/api/recipes/nope", "/api/recipes/..%2fx", "/api/recipes/Bad"):
        assert call(srv, "GET", bad)[0] == 404
    spec = {"name": "mine", "read": ["a"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}
    st, _, out = post(srv, "/api/recipes", spec)
    assert st == 200 and out == {"saved": "mine"} and (tmp_path / "recipes" / "mine.json").is_file()
    assert "mine" in [r["name"] for r in json.loads(call(srv, "GET", "/api/recipes")[2])]
    st, _, out = post(srv, "/api/recipes", {**spec, "name": "../evil"})
    assert st == 400 and "name" in out["error"]
    assert not (tmp_path / "evil.json").exists()


# 4. runs
def test_run_endpoint_uses_model(srv):
    recipe = Recipe.load("support-triage").to_dict()
    qs = recipe["questions"]
    st, _, out = post(srv, "/api/run", {"recipe": recipe, "input": {"type": "csv", "data": CSV_TEXT}})
    assert st == 200 and out["count"] == 8 and len(out["rows"]) == 8
    want = list(csv.DictReader(io.StringIO(CSV_TEXT)))
    # exactly 24 calls, row by row, question by question, with the recipe's own question text, options and state
    expect_calls = [(q["type"] if q["type"] == "yes_no" else "choose", {"subject": r["subject"], "message": r["message"]},
                     q["question"], q.get("options") or q.get("levels")) for r in want for q in qs]
    assert srv.fake.log == expect_calls and len(expect_calls) == 24
    flagged = 0
    for row, src in zip(out["rows"], want):
        state, bad = {"subject": src["subject"], "message": src["message"]}, False
        for q in qs:
            if q["type"] == "yes_no":
                p = fk_yes(state, q["question"])
                assert (row[q["name"]], row[q["name"] + "_p_yes"]) == ("yes" if p >= 0.5 else "no", round(p, 4))
                conf = max(p, 1 - p)
            else:
                pick, conf = fk_choose(state, q["question"], list(q.get("options") or q["levels"]))
                assert row[q["name"]] == pick
            assert row[q["name"] + "_confidence"] == round(conf, 4)
            bad |= conf < 0.6
        assert row["needs_review"] is bad and row["subject"] == src["subject"]
        flagged += bad
    assert 0 < flagged < 8  # the fake really does produce both flagged and clean rows
    assert [r["refund"] for r in out["rows"]] == ["yes", "yes", "no", "yes", "no", "no", "no", "no"]  # a server that says "no" for every row fails here
    for r in out["rows"]:
        p = r["refund_p_yes"]
        assert r["refund_confidence"] == round(max(p, 1 - p), 4) and (p >= 0.9 if r["refund"] == "yes" else p <= 0.2)
    assert len({r["team"] for r in out["rows"]}) > 1 and len({r["tone"] for r in out["rows"]}) > 1
    assert out["columns"][:2] == ["subject", "message"] and {"team", "refund", "tone", "needs_review"} <= set(out["columns"])
    assert out["needs_review"] == flagged and out["ms"] >= 0 and out["failed"] == []


def test_run_by_name_and_flags_review(srv):
    srv.fake.choose = lambda s, q, o: {"choice": list(o)[0], "probabilities": {}, "confidence": 0.3}
    st, _, out = post(srv, "/api/run", {"recipe": "support-triage", "input": {"type": "csv", "data": CSV_TEXT}})
    assert st == 200 and out["needs_review"] == 8


def test_run_input_types(srv):
    r = {"name": "t", "read": ["text"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}
    st, _, out = post(srv, "/api/run", {"recipe": r, "input": {"type": "lines", "data": "one\n\nrefund two\n"}})
    assert st == 200 and [x["q"] for x in out["rows"]] == ["no", "yes"]
    st, _, out = post(srv, "/api/run", {"recipe": r, "input": {"type": "rows", "data": [{"text": "refund"}, {"text": "x", "n": [1]}]}})
    assert st == 200 and out["count"] == 2 and out["columns"][:2] == ["text", "n"]
    buf = io.BytesIO()
    write_xlsx(buf, ["text"], [{"text": "refund please"}])
    b64 = base64.b64encode(buf.getvalue()).decode()
    st, _, out = post(srv, "/api/run", {"recipe": r, "input": {"type": "xlsx", "data": b64}})
    assert st == 200 and out["rows"][0]["q"] == "yes"


@pytest.mark.parametrize("req", [
    {"recipe": "support-triage", "input": {"type": "pdf", "data": "x"}},
    {"recipe": "support-triage", "input": {"type": "xlsx", "data": "not base64!"}},
    {"recipe": "support-triage", "input": {"type": "xlsx", "data": base64.b64encode(b"not a zip").decode()}},
    {"recipe": "/etc/passwd.json", "input": {"type": "lines", "data": "x"}},  # a path is not a recipe name
    {"recipe": "missing-one", "input": {"type": "lines", "data": "x"}},
    {"recipe": {"name": "x"}, "input": {"type": "lines", "data": "x"}},
    {"input": {"type": "lines", "data": "x"}},
])
def test_run_bad_input_is_400_without_echo(srv, req):
    st, _, out = post(srv, "/api/run", req)
    assert st == 400 and out["error"] and "Traceback" not in out["error"]
    assert srv.fake.calls == 0


def test_model_failure_is_a_flagged_row_not_a_crash(srv):
    def boom(*a):
        raise RuntimeError("too long")

    srv.fake.yes_no = boom
    r = {"name": "t", "read": ["text"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}
    st, _, out = post(srv, "/api/run", {"recipe": r, "input": {"type": "lines", "data": "a"}})
    assert st == 200 and out["rows"][0]["needs_review"] is True and "RuntimeError" in out["rows"][0]["error"]


def test_recipe_run_route_csv_and_json(srv):
    st, _, out = post(srv, "/recipes/support-triage/run", CSV_TEXT, {"Content-Type": "text/csv"})
    assert st == 200 and out["count"] == 8 and out["rows"][0]["team"] in ("billing", "shipping", "tech")
    st, _, out = post(srv, "/recipes/support-triage/run", {"rows": [{"subject": "s", "message": "refund me"}]})
    assert st == 200 and out["count"] == 1 and out["rows"][0]["refund"] == "yes"
    assert post(srv, "/recipes/nope/run", {"rows": []})[0] == 400
    assert post(srv, "/recipes/support-triage/run", {"rows": "x"})[0] == 400


def test_preview_lists_columns_and_text_columns(srv):
    data = "id,note,when\n1,hello there,2026-01-01\n2,bye,2026-01-02\n"
    st, _, out = post(srv, "/api/preview", {"input": {"type": "csv", "data": data}})
    assert st == 200 and out["columns"] == ["id", "note", "when"] and out["count"] == 2 and out["text"] == ["note", "when"]
    assert post(srv, "/api/preview", {"input": {"type": "rows", "data": "x"}})[0] == 400
    assert srv.fake.calls == 0


# 5. export
def test_export_csv_guards_formulas(srv):
    st, h, body = call(srv, "POST", "/api/export", {"columns": ["a", "b"], "rows": [{"a": "=1+1", "b": "ok"}], "format": "csv"}, J)
    assert st == 200 and h["Content-Disposition"] == 'attachment; filename="decisions.csv"'
    assert h["Content-Type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    assert rows == [["a", "b"], ["'=1+1", "ok"]]


def test_export_xlsx_guards_formulas(srv):
    import openpyxl

    st, h, body = call(srv, "POST", "/api/export", {"columns": ["a"], "rows": [{"a": "=1+1"}], "format": "xlsx"}, J)
    assert st == 200 and h["Content-Disposition"] == 'attachment; filename="decisions.xlsx"'
    ws = openpyxl.load_workbook(io.BytesIO(body)).active
    assert [c.value for c in ws[2]] == ["'=1+1"]
    assert call(srv, "POST", "/api/export", {"columns": ["a"], "rows": [], "format": "pdf"}, J)[0] == 400


# 6. security
def test_rejects_rebinding_host(srv):
    for method, path in (("GET", "/"), ("GET", "/api/recipes"), ("POST", "/decide")):
        st, _, body = call(srv, method, path, '{"question": "q"}' if method == "POST" else None, {**J, "Host": "evil.example"})
        assert st == 403, (method, path)
    assert call(srv, "GET", "/api/status", None, {"Host": f"evil.example:{srv.server_address[1]}"})[0] == 403
    for ok in ("localhost", "127.0.0.1", "localhost:1", "[::1]:80"):
        assert call(srv, "GET", "/api/status", None, {"Host": ok})[0] == 200
    assert srv.fake.calls == 0


def test_explicit_host_allows_other_host_names(tmp_path):
    s = make_server(Fake(), port=0, explicit_host=True)
    threading.Thread(target=s.serve_forever, args=(0.01,), daemon=True).start()
    try:
        assert call(s, "GET", "/api/status", None, {"Host": "my-mac.local:8000"})[0] == 200
        # same-origin is still enforced against the Host header
        bad = {**J, "Host": "my-mac.local:8000", "Origin": "http://evil.example"}
        assert call(s, "POST", "/decide", {"question": "q"}, bad)[0] == 403
        good = {**J, "Host": "my-mac.local:8000", "Origin": "http://my-mac.local:8000"}
        assert call(s, "POST", "/decide", {"question": "q"}, good)[0] == 200
    finally:
        s.shutdown()
        s.server_close()


def test_rejects_cross_origin_post(srv):
    body = {"state": "x", "question": "Refund?"}
    for origin in ("http://evil.example", "null", "https://127.0.0.1", "http://127.0.0.1:1"):
        assert call(srv, "POST", "/decide", body, {**J, "Origin": origin})[0] == 403, origin
    assert call(srv, "POST", "/decide", body, {**J, "Sec-Fetch-Site": "cross-site"})[0] == 403
    port = srv.server_address[1]
    assert call(srv, "POST", "/decide", body, {**J, "Origin": f"http://127.0.0.1:{port}", "Host": f"127.0.0.1:{port}"})[0] == 200
    assert call(srv, "POST", "/decide", body, {**J, "Sec-Fetch-Site": "same-origin"})[0] == 200
    assert srv.fake.calls == 2


def test_rejects_form_content_type(srv):
    for ctype in ("application/x-www-form-urlencoded", "multipart/form-data; boundary=x", "text/plain"):
        st, _, _ = call(srv, "POST", "/decide", '{"question": "q"}', {"Content-Type": ctype})
        assert st in (403, 415), ctype
    assert call(srv, "POST", "/decide", '{"question": "q"}')[0] in (403, 415)  # no content type at all
    assert srv.fake.calls == 0


def test_other_methods_are_refused_and_no_cors(srv):
    for m in ("PUT", "DELETE", "PATCH"):
        st, h, _ = call(srv, m, "/api/recipes", "{}", J)
        assert st == 405 and not any(k.lower().startswith("access-control") for k in h)
    st, h, _ = call(srv, "POST", "/decide", '{"question": "q"}', {**J, "Origin": "http://evil.example"})
    assert st == 403 and not any(k.lower().startswith("access-control") for k in h)
    st, h, _ = call(srv, "OPTIONS", "/decide", None, {"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"})
    assert st == 405 and not any(k.lower().startswith("access-control") for k in h)
    assert call(srv, "PUT", "/decide", "{}", {**J, "Host": "evil.example"})[0] == 403


def test_unknown_post_is_404_and_errors_never_echo_body(srv):
    assert post(srv, "/nope", {"a": 1})[0] == 404
    st, _, body = call(srv, "POST", "/decide", "SECRET-PAYLOAD{", J)
    assert st == 400 and b"SECRET-PAYLOAD" not in body
    st, _, out = post(srv, "/api/run", {"recipe": "SECRET-NAME-x", "input": {"type": "lines", "data": "x"}})
    assert st == 400 and "Traceback" not in out["error"]


# ---- T2-fix1: protocol errors, other methods, validation, fixed error text
SEC = ("X-Content-Type-Options", "Cache-Control", "Referrer-Policy", "X-Frame-Options")


def raw(s, data, n=4096):
    import socket

    sock = socket.create_connection(("127.0.0.1", s.server_address[1]), timeout=10)
    sock.sendall(data)
    out = b""
    while True:
        chunk = sock.recv(n)
        if not chunk:
            break
        out += chunk
    sock.close()
    return out


def test_unsupported_methods_get_json_security_headers_and_host_check(srv):
    for m in ("TRACE", "FOO", "CONNECT"):
        st, h, body = call(srv, m, "/", None, J)
        assert st in (405, 501) and json.loads(body)["error"], m
        assert all(k in h for k in SEC) and h["Content-Type"].startswith("application/json")
    st, h, body = call(srv, "TRACE", "/", None, {**J, "Host": "evil.example"})
    assert st == 403 and json.loads(body)["error"] and all(k in h for k in SEC)
    assert call(srv, "TRACE", "/", None, {**J, "Origin": "http://evil.example"})[0] == 403


def test_malformed_requests_get_json_not_stdlib_html(srv):
    for data in (b"GET /" + b"a" * 70000 + b" HTTP/1.0\r\n\r\n",
                 b"GET / HTTP/1.0\r\n" + b"X: y\r\n" * 200 + b"\r\n"):
        out = raw(srv, data)
        head, _, body = out.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.") and b"text/html" not in head and b"<html" not in body.lower(), data[:20]
        assert json.loads(body)["error"]
        assert all(k.encode().lower() in head.lower() for k in SEC)
    for data in (b"GARBAGE x y\r\n\r\n", b"GET / HTTP/9.9\r\nHost: x\r\n\r\n"):  # no usable version: HTTP/0.9 has no headers, the body is still ours
        out = raw(srv, data)
        assert json.loads(out)["error"] and b"<html" not in out.lower()


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_state_changing_methods_get_origin_and_content_type_checks(srv, method):
    assert call(srv, method, "/api/recipes", "{}", {**J, "Origin": "http://evil.example"})[0] == 403
    assert call(srv, method, "/api/recipes", "{}", {**J, "Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call(srv, method, "/api/recipes", "{}", {"Content-Type": "application/x-www-form-urlencoded"})[0] in (403, 415)
    assert call(srv, method, "/api/recipes", "{}", J)[0] == 405


@pytest.mark.parametrize("body", [
    {"question": None, "state": 42, "options": False},
    {"question": "q", "state": 42},
    {"question": "q", "state": None},
    {"question": 5},
    {"question": "   "},
    {"question": "q", "options": False},
    {"question": "q", "options": "ab"},
    {"question": "q", "options": [1, 2]},
    {"question": "q", "options": ["a"]},
    {"question": "q", "options": ["a", ""]},
    {"question": "q", "options": {"a": 1, "b": "x"}},
    {"question": "q", "options": [["a"], ["b"]]},
])
def test_decide_validates_before_any_model_call(srv, body):
    st, _, out = post(srv, "/decide", body)
    assert st == 400 and out["error"] and srv.fake.calls == 0


def test_decide_accepts_dict_options_and_empty_options_means_yes_no(srv):
    st, _, out = post(srv, "/decide", {"state": "s", "question": "q", "options": {"a": "A: one", "b": "B: two"}})
    assert st == 200 and out["choice"] in ("a", "b")
    st, _, out = post(srv, "/decide", {"state": "s", "question": "q", "options": []})
    assert st == 200 and out["answer"] in ("yes", "no")


@pytest.mark.parametrize("exc", [TypeError, KeyError, AttributeError, ValueError, csv.Error, RuntimeError])
def test_model_exception_text_never_reaches_the_client(srv, exc):
    def boom(*a):
        raise exc("SECRET-PAYLOAD")

    srv.fake.choose = srv.fake.yes_no = boom
    for body in ({"state": "x", "question": "q", "options": ["a", "b"]}, {"state": "x", "question": "q"}):
        st, _, raw_body = call(srv, "POST", "/decide", body, J)
        assert st == 500 and b"SECRET-PAYLOAD" not in raw_body and exc.__name__.encode() not in raw_body


@pytest.mark.parametrize("path,body", [
    ("/decide", [1]), ("/decide", "x"), ("/api/run", {"input": {"type": "lines", "data": "x"}}),
    ("/api/run", [1]), ("/api/run", {"recipe": ["SECRET-PAYLOAD"], "input": "SECRET-PAYLOAD"}),
    ("/api/preview", {"input": None}), ("/api/preview", {}), ("/api/recipes", {"name": "x", "questions": [None]}),
    ("/api/export", [1]), ("/api/export", {"columns": 5, "rows": [], "format": "csv"}),
    ("/recipes/support-triage/run", {"rows": [1]}), ("/recipes/support-triage/run", {}),
])
def test_bad_bodies_get_fixed_messages_not_exception_names(srv, path, body):
    st, _, out = post(srv, path, body)
    assert st == 400 and out["error"] and not any(w in out["error"] for w in ("Error", "KeyError", "SECRET", "'"))


def test_csv_parse_error_is_a_fixed_message(srv):
    big = "a\n" + "x" * 200000 + "\n"  # csv.Error: field larger than field limit
    st, _, out = post(srv, "/api/run", {"recipe": "support-triage", "input": {"type": "csv", "data": big}})
    assert (st, out["error"]) == (400, "cannot read the CSV data")


# ---- limits
def test_limit_constants():
    from decision_tune import server

    assert (server.MAX_ROWS, server.MAX_COLUMNS, server.MAX_CELLS, server.MAX_DECISIONS) == (100_000, 1_000, 5_000_000, 2_000_000)
    assert (server.MAX_ZIP_TOTAL, server.MAX_ZIP_MEMBER) == (200 << 20, 100 << 20)
    assert (server.READ_TIMEOUT, server.MAX_ACTIVE) == (30, 16)


def _limit(st, out):
    assert st == 413 and out["error"] and "Traceback" not in out["error"]
    return out["error"]


@pytest.mark.parametrize("rows,cols,named", [(100_001, 1, "100,000 rows"), (1, 1_001, "1,000 columns"), (5_001, 1_000, "5,000,000")])
def test_export_dimension_limits(srv, rows, cols, named):
    names = [f"c{i}" for i in range(cols)]
    st, h, body = call(srv, "POST", "/api/export", json.dumps({"columns": names, "rows": [{}] * rows, "format": "csv"}), J)
    assert st == 413 and h["Content-Type"].startswith("application/json")
    assert named in json.loads(body)["error"]


def test_export_rejects_repeated_columns(srv):
    st, _, out = post(srv, "/api/export", {"columns": ["a"] * 1000, "rows": [{"a": "x" * 1000}] * 100, "format": "csv"})
    assert st == 400 and out["error"]


def test_export_within_limits_still_works(srv):
    st, _, body = call(srv, "POST", "/api/export", json.dumps({"columns": ["a", "b"], "rows": [{"a": 1, "b": 2}] * 1000, "format": "csv"}), J)
    assert st == 200 and body.count(b"\n") == 1001


def test_csv_and_rows_input_limits(srv, monkeypatch):
    from decision_tune import server

    monkeypatch.setattr(server, "MAX_ROWS", 5)
    r = {"name": "t", "read": ["a"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}
    over = "a\n" + "x\n" * 6
    for path, body in (("/api/run", {"recipe": r, "input": {"type": "csv", "data": over}}),
                       ("/api/preview", {"input": {"type": "csv", "data": over}}),
                       ("/api/run", {"recipe": r, "input": {"type": "rows", "data": [{"a": "x"}] * 6}}),
                       ("/api/run", {"recipe": r, "input": {"type": "lines", "data": "x\n" * 6}})):
        _limit(*post(srv, path, body)[::2])
    assert call(srv, "POST", "/recipes/support-triage/run", "subject,message\n" + "a,b\n" * 6, {"Content-Type": "text/csv"})[0] == 413
    ok = post(srv, "/api/run", {"recipe": r, "input": {"type": "csv", "data": "a\n" + "x\n" * 5}})
    assert ok[0] == 200 and srv.fake.calls == 5


def test_input_column_and_cell_limits(srv, monkeypatch):
    from decision_tune import server

    monkeypatch.setattr(server, "MAX_COLUMNS", 3)
    monkeypatch.setattr(server, "MAX_CELLS", 10)
    r = {"name": "t", "read": ["a"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}
    _limit(*post(srv, "/api/preview", {"input": {"type": "csv", "data": "a,b,c,d\n1,2,3,4\n"}})[::2])
    _limit(*post(srv, "/api/preview", {"input": {"type": "csv", "data": "a,b,c\n" + "1,2,3\n" * 4}})[::2])  # 12 cells
    assert post(srv, "/api/preview", {"input": {"type": "csv", "data": "a,b,c\n" + "1,2,3\n" * 3}})[0] == 200
    assert srv.fake.calls == 0


def test_decision_budget(srv, monkeypatch):
    from decision_tune import server

    monkeypatch.setattr(server, "MAX_DECISIONS", 20)  # 8 rows x 3 questions = 24
    _limit(*post(srv, "/api/run", {"recipe": "support-triage", "input": {"type": "csv", "data": CSV_TEXT}})[::2])
    _limit(*post(srv, "/recipes/support-triage/run", {"rows": [{"subject": "s", "message": "m"}] * 8})[::2])
    assert srv.fake.calls == 0
    monkeypatch.setattr(server, "MAX_DECISIONS", 24)
    assert post(srv, "/api/run", {"recipe": "support-triage", "input": {"type": "csv", "data": CSV_TEXT}})[0] == 200


def _zip_b64(members):
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, size in members:
            z.writestr(name, b"\0" * size)
    return base64.b64encode(buf.getvalue()).decode()


@pytest.mark.parametrize("members", [[("big.xml", (100 << 20) + 1)], [("a", 70 << 20), ("b", 70 << 20), ("c", 70 << 20)]],
                         ids=["member-over-100MB", "total-over-200MB"])
def test_xlsx_zip_bomb_is_refused_before_openpyxl(srv, members, monkeypatch):
    import openpyxl

    def never(*a, **k):
        raise AssertionError("openpyxl must not see a bomb")

    monkeypatch.setattr(openpyxl, "load_workbook", never)
    data = _zip_b64(members)
    assert len(data) < 5 << 20  # a tiny upload that expands past the cap
    for path, body in (("/api/preview", {"input": {"type": "xlsx", "data": data}}),
                       ("/api/run", {"recipe": "support-triage", "input": {"type": "xlsx", "data": data}})):
        err = _limit(*post(srv, path, body)[::2])
        assert "MB" in err
    assert srv.fake.calls == 0


def _xlsx_b64(rows, cols=("a",)):
    buf = io.BytesIO()
    write_xlsx(buf, list(cols), rows)
    return base64.b64encode(buf.getvalue()).decode()


def test_xlsx_row_and_column_limits_are_enforced_while_reading(srv, monkeypatch):
    from decision_tune import server

    r = {"name": "t", "read": ["a"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}
    monkeypatch.setattr(server, "MAX_ROWS", 50)
    over = _xlsx_b64([{"a": f"v{i}"} for i in range(51)])
    for path, body in (("/api/preview", {"input": {"type": "xlsx", "data": over}}),
                       ("/api/run", {"recipe": r, "input": {"type": "xlsx", "data": over}})):
        _limit(*post(srv, path, body)[::2])
    ok = _xlsx_b64([{"a": f"v{i}"} for i in range(50)])
    st, _, out = post(srv, "/api/run", {"recipe": r, "input": {"type": "xlsx", "data": ok}})
    assert st == 200 and out["count"] == 50 and srv.fake.calls == 50
    monkeypatch.setattr(server, "MAX_COLUMNS", 2)
    wide = _xlsx_b64([{"a": 1, "b": 2, "c": 3}], cols=("a", "b", "c"))
    _limit(*post(srv, "/api/preview", {"input": {"type": "xlsx", "data": wide}})[::2])


def test_xlsx_blank_rows_count_toward_the_row_limit(srv, monkeypatch):
    from decision_tune import server

    monkeypatch.setattr(server, "MAX_ROWS", 10)
    buf = io.BytesIO()
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["a"])
    ws.cell(row=500, column=1, value="late")  # 498 blank rows, then data
    wb.save(buf)
    _limit(*post(srv, "/api/preview", {"input": {"type": "xlsx", "data": base64.b64encode(buf.getvalue()).decode()}})[::2])


# ---- connection deadline and bounded admission
def test_stalled_body_gets_408_and_frees_the_thread(srv, monkeypatch):
    import socket

    from decision_tune import server

    monkeypatch.setattr(server.Handler, "timeout", 0.5)
    sock = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=10)
    sock.sendall(b"POST /decide HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{\"q")
    out = b""
    while chunk := sock.recv(4096):  # the server must hang up on its own
        out += chunk
    sock.close()
    assert out.startswith(b"HTTP/1.0 408") and b"error" in out and srv.fake.calls == 0


def test_at_most_16_requests_at_once_extras_get_503(srv, monkeypatch):
    import socket
    import time

    from decision_tune import server

    monkeypatch.setattr(server.Handler, "timeout", 20)
    idle = [socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=10) for _ in range(server.MAX_ACTIVE)]
    try:
        st, h, body = call(srv, "GET", "/api/status")
        assert st == 503 and json.loads(body)["error"] and all(k in h for k in SEC)
    finally:
        for s in idle:
            s.close()
    for _ in range(40):  # slots come back once the idle connections are gone
        if call(srv, "GET", "/api/status")[0] == 200:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("slots were not released")


# ---- GET failures
def test_recipe_get_failures_are_fixed_json(srv, monkeypatch):
    from decision_tune import server

    def deny(*a, **k):
        raise PermissionError("/secret/path")

    monkeypatch.setattr(server, "list_recipes", deny)
    st, h, body = call(srv, "GET", "/api/recipes")
    assert st == 500 and json.loads(body)["error"] and b"/secret" not in body and all(k in h for k in SEC)
    monkeypatch.setattr(server.Recipe, "load", deny)
    st, _, body = call(srv, "GET", "/api/recipes/support-triage")
    assert st == 500 and json.loads(body)["error"] and b"/secret" not in body
    st, _, body = call(srv, "POST", "/api/run", {"recipe": "support-triage", "input": {"type": "lines", "data": "x"}}, J)
    assert st == 500 and b"/secret" not in body  # the same boundary on POST


# ---- failed rows are reported apart from source columns
def test_input_column_named_error_is_not_a_failure(srv):
    csv_in = "error,text\nkeep me,refund please\n,hello\n"
    r = {"name": "t", "read": ["text"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}
    st, _, out = post(srv, "/api/run", {"recipe": r, "input": {"type": "csv", "data": csv_in}})
    assert st == 200 and out["failed"] == [] and out["rows"][0]["error"] == "keep me" and out["rows"][0]["q"] == "yes"
    srv.fake.yes_no = lambda *a: (_ for _ in ()).throw(RuntimeError("too long"))
    st, _, out = post(srv, "/api/run", {"recipe": r, "input": {"type": "csv", "data": csv_in}})
    assert out["failed"] == [0, 1] and out["rows"][0]["error"].startswith("RuntimeError") and out["needs_review"] == 2


# ---- T2-fix2: a data row wider than the limit is refused even when the header is narrow
def _wide_row_xlsx(width):
    import openpyxl

    wb = openpyxl.Workbook()  # in memory: header width 1, then one row of the given width
    ws = wb.active
    ws.append(["a"])
    ws.append(["v"] * width)
    raw = io.BytesIO()
    wb.save(raw)
    out = io.BytesIO()
    with zipfile.ZipFile(raw) as zin, zipfile.ZipFile(out, "w") as zout:  # drop the stored dimensions, as a hostile file would
        for i in zin.infolist():
            data = zin.read(i.filename)
            zout.writestr(i, re.sub(rb"<dimension[^>]*/>", b"", data) if i.filename.startswith("xl/worksheets/") else data)
    return base64.b64encode(out.getvalue()).decode()


@pytest.mark.parametrize("lowered", ["MAX_COLUMNS", "MAX_CELLS"])
def test_xlsx_wide_data_row_is_refused_with_a_narrow_header(srv, monkeypatch, lowered):
    from decision_tune import server

    monkeypatch.setattr(server, "MAX_COLUMNS", 5)
    if lowered == "MAX_CELLS":
        monkeypatch.setattr(server, "MAX_CELLS", 1)
    wide = _wide_row_xlsx(6)  # MAX_COLUMNS + 1
    _limit(*post(srv, "/api/preview", {"input": {"type": "xlsx", "data": wide}})[::2])
    st, _, out = post(srv, "/api/preview", {"input": {"type": "xlsx", "data": _wide_row_xlsx(5)}})
    if lowered == "MAX_COLUMNS":
        assert st == 200 and out["columns"] == ["a"] and out["count"] == 1  # a row exactly at the limit is fine


# ---- GATE-fix1
def test_run_with_absent_read_columns_is_400_and_never_reaches_the_model(srv):
    for st, _, out in (post(srv, "/api/run", {"recipe": "support-triage", "input": {"type": "csv", "data": "text\nrefund please\n"}}),
                       post(srv, "/api/run", {"recipe": "support-triage", "input": {"type": "lines", "data": "refund please"}}),
                       post(srv, "/api/run", {"recipe": "support-triage", "input": {"type": "rows", "data": [{"text": "x"}]}}),
                       post(srv, "/recipes/support-triage/run", {"rows": [{"text": "x"}]}),
                       post(srv, "/api/run", {"recipe": "support-triage", "input": {"type": "csv", "data": "text\n"}})):  # header only
        assert (st, out["error"]) == (400, "missing columns: subject, message")
    assert srv.fake.calls == 0


def _ids(srv, n=1, data=CSV_TEXT):
    return [post(srv, "/api/run", {"recipe": "support-triage", "input": {"type": "csv", "data": data}})[2]["run_id"] for _ in range(n)]


def test_every_run_can_be_downloaded_by_its_run_id(srv):
    (rid,) = _ids(srv)
    st, h, body = call(srv, "GET", f"/api/export?run={rid}&format=csv")
    assert st == 200 and h["Content-Disposition"] == 'attachment; filename="decisions.csv"' and h["Content-Type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    assert len(rows) == 9 and rows[0][:2] == ["subject", "message"] and "team" in rows[0] and rows[1][0] == "Order #4417"
    import openpyxl

    st, h, body = call(srv, "GET", f"/api/export?run={rid}&format=xlsx")
    assert st == 200 and openpyxl.load_workbook(io.BytesIO(body)).active.max_row == 9
    st, _, out = post(srv, "/recipes/support-triage/run", {"rows": [{"subject": "=1+1", "message": "m"}]})
    assert call(srv, "GET", f"/api/export?run={out['run_id']}&format=csv")[2].decode("utf-8-sig").splitlines()[1].startswith("'=1+1")  # formula guard


def test_export_by_run_id_is_not_limited_by_result_expansion(srv):
    row = {f"c{i}": "x" for i in range(998)} | {"subject": "s", "message": "refund"}  # 1,000 columns: accepted as input
    st, _, out = post(srv, "/api/run", {"recipe": "support-triage", "input": {"type": "rows", "data": [row]}})
    assert st == 200 and len(out["columns"]) > 1000
    st, _, body = call(srv, "GET", f"/api/export?run={out['run_id']}&format=csv")
    assert st == 200 and len(next(csv.reader(io.StringIO(body.decode("utf-8-sig"))))) == len(out["columns"])


def test_only_the_last_eight_runs_are_kept(srv):
    ids = _ids(srv, 9, "subject,message\na,b\n")
    assert len(set(ids)) == 9 and all(re.fullmatch(r"[0-9a-f]{16}", i) for i in ids)
    assert call(srv, "GET", f"/api/export?run={ids[0]}&format=csv")[0] == 404
    assert all(call(srv, "GET", f"/api/export?run={i}&format=csv")[0] == 200 for i in ids[1:])


@pytest.mark.parametrize("q", ["", "?run=nope&format=csv", "?run=0123456789abcdef&format=csv", "?format=csv", "?run=%00&format=csv"])
def test_export_get_unknown_run_is_404_json(srv, q):
    st, h, body = call(srv, "GET", "/api/export" + q)
    assert st == 404 and h["Content-Type"].startswith("application/json") and json.loads(body)["error"]


def test_export_get_bad_format_is_400(srv):
    (rid,) = _ids(srv)
    assert call(srv, "GET", f"/api/export?run={rid}&format=pdf")[0] == 400


def test_decide_rejects_bad_options_everywhere_it_is_checked(srv):
    for opts in (["a"], ["", "b"], ["a", "a"], [str(i) for i in range(33)], {"": "x", "b": "y"}):
        assert post(srv, "/decide", {"state": "s", "question": "q?", "options": opts})[0] == 400
    assert srv.fake.calls == 0
