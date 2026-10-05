import base64
import csv
import http.client
import io
import json
import os
import threading

import pytest

from decision_tune.recipe import Recipe, write_xlsx
from decision_tune.server import make_server

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tickets.csv")
CSV_TEXT = open(DATA, encoding="utf-8").read()
J = {"Content-Type": "application/json"}


class Fake:
    """Answers are a pure function of the input, so the test can recompute them. Counts every call."""

    def __init__(self):
        self.calls = 0
        self.backend_name = "fake"

    def choose(self, state, question, options):
        self.calls += 1
        keys = list(options)
        pick = keys[len(str(state)) % len(keys)]
        return {"choice": pick, "probabilities": {k: 1 / len(keys) for k in keys}, "confidence": 0.9}

    def yes_no(self, state, question):
        self.calls += 1
        return 0.95 if "refund" in str(state) else 0.1


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


def scripted(row):
    state = {"subject": row["subject"], "message": row["message"]}
    team = ["billing", "shipping", "tech"][len(str(state)) % 3]
    refund = "yes" if "refund" in str(state) else "no"
    tone = ["neg", "neu", "pos"][len(str(state)) % 3]
    return team, refund, tone


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
    assert st == 200 and out["answer"] == "yes" and out["p_yes"] == 0.95 and "ms" in out


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
    st, _, out = post(srv, "/api/run", {"recipe": recipe, "input": {"type": "csv", "data": CSV_TEXT}})
    assert st == 200 and out["count"] == 8 and len(out["rows"]) == 8
    assert srv.fake.calls == 24  # 8 rows x 3 questions, one model call each
    want = list(csv.DictReader(io.StringIO(CSV_TEXT)))
    for row, src in zip(out["rows"], want):
        team, refund, tone = scripted(src)
        assert (row["team"], row["refund"], row["tone"]) == (team, refund, tone)
        assert row["subject"] == src["subject"] and row["needs_review"] is False
    assert out["columns"][:2] == ["subject", "message"] and {"team", "refund", "tone", "needs_review"} <= set(out["columns"])
    assert out["needs_review"] == 0 and out["ms"] >= 0


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
