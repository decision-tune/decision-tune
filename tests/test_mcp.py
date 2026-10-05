import csv
import io
import json
import os
import sys

import openpyxl
import pytest

from decision_tune import __version__
from decision_tune.mcp import run_stdio

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tickets.csv")
OPTS = {"billing": "Billing: charges", "shipping": "Shipping: delivery", "tech": "Tech support: bugs"}


class Fake:
    """Scripted answers: choose picks 'shipping', yes_no says 0.9."""

    def __init__(self):
        self.calls = []

    def choose(self, state, question, options):
        self.calls.append(("choose", state, question, options))
        keys = list(options)
        probs = {k: 0.1 for k in keys}
        probs["shipping" if "shipping" in keys else keys[0]] = 0.8
        best = max(probs, key=probs.get)
        return {"choice": best, "probabilities": probs, "confidence": probs[best]}

    def yes_no(self, state, question):
        self.calls.append(("yes_no", state, question))
        return 0.9


def rpc(*msgs, factory=None):
    """Send each message (dict or raw str) as one line; return (parsed response lines, raw stdout, factory call count)."""
    fake, n = Fake(), []

    def make():
        n.append(1)
        return (factory or (lambda: fake))()

    lines = "".join((m if isinstance(m, str) else json.dumps(m)) + "\n" for m in msgs)
    out = io.StringIO()
    run_stdio(make, io.StringIO(lines), out)
    return [json.loads(x) for x in out.getvalue().splitlines()], out.getvalue(), len(n)


def req(id_, method, **params):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params}


def call(id_, name, **args):
    return req(id_, "tools/call", name=name, arguments=args)


def one(msg):
    (r,), _, _ = rpc(msg)
    return r


def test_initialize_negotiates_version():
    r = one(req(1, "initialize", protocolVersion="2025-03-26", capabilities={}, clientInfo={"name": "t", "version": "1"}))
    res = r["result"]
    assert r["id"] == 1 and r["jsonrpc"] == "2.0"
    assert res["protocolVersion"] == "2025-03-26"
    assert res["capabilities"] == {"tools": {}}
    assert res["serverInfo"] == {"name": "decisiontune", "version": __version__}
    assert isinstance(res["instructions"], str) and res["instructions"]
    assert one(req(2, "initialize", protocolVersion="2099-01-01"))["result"]["protocolVersion"] == "2025-06-18"
    assert one(req(3, "initialize"))["result"]["protocolVersion"] == "2025-06-18"


def test_notification_gets_no_output():
    out, raw, _ = rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert out == [] and raw == ""


def test_ping_and_blank_lines():
    out, _, _ = rpc("", req(1, "ping"), "   ")
    assert out == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_tools_list():
    tools = one(req(1, "tools/list"))["result"]["tools"]
    assert [t["name"] for t in tools] == ["decide", "run_recipe", "list_recipes"]
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"
    assert tools[0]["inputSchema"]["required"] == ["state", "question"]
    assert tools[1]["inputSchema"]["required"] == ["recipe"]


def test_decide_choose():
    res = one(call(1, "decide", state="The order arrived broken.", question="Which team?", options=OPTS))["result"]
    assert res["isError"] is False
    s = res["structuredContent"]
    assert s["choice"] == "shipping" and s["confidence"] == 0.8 and set(s["probabilities"]) == set(OPTS) and s["ms"] >= 0
    text = res["content"][0]
    assert text["type"] == "text" and "shipping" in text["text"] and json.loads(text["text"].split("\n", 1)[1]) == s


def test_decide_choose_list_options():
    s = one(call(1, "decide", state={"a": 1}, question="Which?", options=["x", "y"]))["result"]["structuredContent"]
    assert s["choice"] == "x"


def test_decide_yes_no():
    res = one(call(1, "decide", state="Please refund me.", question="Is it a refund?"))["result"]
    assert res["isError"] is False
    assert res["structuredContent"]["answer"] == "yes" and res["structuredContent"]["p_yes"] == 0.9 and "ms" in res["structuredContent"]


@pytest.mark.parametrize("args", [
    {"question": "q"},
    {"state": "s"},
    {"state": 5, "question": "q"},
    {"state": "s", "question": "q", "options": "billing"},
    {"state": "s", "question": "q", "options": [1, 2]},
])
def test_decide_bad_input_is_a_tool_error(args):
    res = one(call(1, "decide", **args))["result"]
    assert res["isError"] is True and "Traceback" not in res["content"][0]["text"] and "\n" not in res["content"][0]["text"]


def test_run_recipe_on_csv():
    res = one(call(1, "run_recipe", recipe="support-triage", input_path=DATA))["result"]
    assert res["isError"] is False
    s = res["structuredContent"]
    assert s["count"] == 8 and len(s["rows"]) == 8 and s["truncated"] is False and "output_path" not in s
    assert s["rows"][0]["team"] == "shipping" and s["rows"][0]["refund"] == "yes" and s["rows"][0]["tone"] in ("neg", "neu", "pos")
    assert isinstance(s["needs_review"], int) and s["ms"] >= 0


def test_run_recipe_with_rows_and_recipe_object():
    recipe = {"name": "t", "read": ["a"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}
    s = one(call(1, "run_recipe", recipe=recipe, rows=[{"a": "x"}, {"a": "y"}]))["result"]["structuredContent"]
    assert s["count"] == 2 and s["rows"][1] == {"a": "y", "q": "yes", "q_p_yes": 0.9, "q_confidence": 0.9, "needs_review": False}


def test_run_recipe_truncates_to_200_rows():
    recipe = {"name": "t", "read": ["a"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}
    s = one(call(1, "run_recipe", recipe=recipe, rows=[{"a": str(i)} for i in range(205)]))["result"]["structuredContent"]
    assert s["count"] == 205 and len(s["rows"]) == 200 and s["truncated"] is True


def test_run_recipe_writes_csv(tmp_path):
    out = tmp_path / "tickets-decided.csv"
    s = one(call(1, "run_recipe", recipe="support-triage", input_path=DATA, output_path=str(out)))["result"]["structuredContent"]
    assert s["output_path"] == str(out)
    got = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert len(got) == 8 and got[0]["team"] == "shipping" and "needs_review" in got[0] and "team_confidence" in got[0]
    # a -decided file may be replaced
    one(call(1, "run_recipe", recipe="support-triage", input_path=DATA, output_path=str(out)))
    assert len(list(csv.DictReader(open(out, encoding="utf-8-sig")))) == 8


def test_run_recipe_writes_xlsx_with_formula_guard(tmp_path):
    out = tmp_path / "x.xlsx"
    recipe = {"name": "t", "read": ["a"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}
    res = one(call(1, "run_recipe", recipe=recipe, rows=[{"a": "=1+1"}], output_path=str(out)))["result"]
    assert res["isError"] is False
    ws = openpyxl.load_workbook(out).active
    assert [c.value for c in ws[1]][:2] == ["a", "q"] and ws["A2"].value == "'=1+1"


def test_run_recipe_expands_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "t.csv").write_text("subject,message\nhi,Where is my order?\n")
    s = one(call(1, "run_recipe", recipe="support-triage", input_path="~/t.csv"))["result"]["structuredContent"]
    assert s["count"] == 1


def test_output_path_never_clobbers(tmp_path):
    keep = tmp_path / "mine.csv"
    keep.write_text("precious")
    link = tmp_path / "evil-decided.csv"
    link.symlink_to(keep)
    for bad in (keep, link, tmp_path / "no" / "such" / "x.csv", tmp_path / "x.txt"):
        res = one(call(1, "run_recipe", recipe="support-triage", input_path=DATA, output_path=str(bad)))["result"]
        assert res["isError"] is True, bad
    assert keep.read_text() == "precious"
    assert not (tmp_path / "x.txt").exists()


def test_output_path_checked_before_the_model_runs(tmp_path):
    keep = tmp_path / "mine.csv"
    keep.write_text("precious")
    _, _, loads = rpc(call(1, "run_recipe", recipe="support-triage", input_path=DATA, output_path=str(keep)))
    assert loads == 0


@pytest.mark.parametrize("args", [
    {"recipe": "no-such-recipe", "rows": []},
    {"recipe": "support-triage"},
    {"recipe": "support-triage", "rows": [], "input_path": DATA},
    {"recipe": "support-triage", "input_path": "/no/such/file.csv"},
    {"recipe": "support-triage", "input_path": DATA + ".txt"},
    {"recipe": "support-triage", "rows": ["not an object"]},
    {"recipe": {"name": "bad"}, "rows": []},
    {"recipe": 5, "rows": []},
])
def test_run_recipe_bad_input_is_a_tool_error(args):
    res = one(call(1, "run_recipe", **args))["result"]
    assert res["isError"] is True and "\n" not in res["content"][0]["text"] and "Traceback" not in res["content"][0]["text"]


def test_list_recipes(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_TUNE_HOME", str(tmp_path))
    s = one(call(1, "list_recipes"))["result"]["structuredContent"]
    assert "support-triage" in [r["name"] for r in s["recipes"]]


def test_unknown_tool_and_method_and_bad_json():
    res = one(call(1, "nope"))["result"]
    assert res["isError"] is True and "nope" in res["content"][0]["text"]
    r = one(req(2, "resources/list"))
    assert r["id"] == 2 and r["error"]["code"] == -32601 and "result" not in r
    r = one("{not json")
    assert r["id"] is None and r["error"]["code"] == -32700
    assert one("[1, 2]")["error"]["code"] == -32600
    assert one({"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": "x"})["error"]["code"] == -32602


def test_every_request_gets_exactly_one_response_in_order():
    out, _, _ = rpc(req(1, "ping"), {"jsonrpc": "2.0", "method": "notifications/initialized"}, "{bad", req("b", "ping"),
                    call(3, "nope"))
    assert [r["id"] for r in out] == [1, None, "b", 3]


def test_model_loaded_lazily_and_used():
    fake = Fake()
    n = []

    def make():
        n.append(1)
        return fake

    msgs = [req(1, "initialize", protocolVersion="2025-06-18"), {"jsonrpc": "2.0", "method": "notifications/initialized"},
            req(2, "tools/list"), req(3, "ping"), call(4, "list_recipes")]
    out = io.StringIO()
    run_stdio(make, io.StringIO("".join(json.dumps(m) + "\n" for m in msgs)), out)
    assert len(n) == 0
    msgs = msgs[:3] + [call(4, "decide", state="The order arrived broken.", question="Which team?", options=OPTS),
                       call(5, "decide", state="Broken again.", question="Which team?", options=OPTS)]
    out = io.StringIO()
    n.clear()
    run_stdio(make, io.StringIO("".join(json.dumps(m) + "\n" for m in msgs)), out)
    got = [json.loads(x) for x in out.getvalue().splitlines()]
    assert len(n) == 1 and len(fake.calls) == 2
    assert got[-2]["result"]["structuredContent"]["choice"] == "shipping" == got[-1]["result"]["structuredContent"]["choice"]


def test_model_load_failure_is_a_tool_error_and_retried():
    n = []

    def make():
        n.append(1)
        raise RuntimeError("DecisionTune 1.0 is not downloaded yet.\nRun the download command.")

    out, _, _ = rpc(call(1, "decide", state="s", question="q"), call(2, "decide", state="s", question="q"), req(3, "ping"), factory=make)
    assert [r["id"] for r in out] == [1, 2, 3]
    assert out[0]["result"]["isError"] is True and out[0]["result"]["content"][0]["text"] == "DecisionTune 1.0 is not downloaded yet."
    assert out[2]["result"] == {}


def test_stdout_is_protocol_only(capsys):
    class Noisy(Fake):
        pass

    def make():
        print("downloading the model...")  # a library printing to stdout must not reach the protocol stream
        return Noisy()

    msgs = [req(1, "initialize", protocolVersion="2025-06-18"), {"jsonrpc": "2.0", "method": "notifications/initialized"},
            req(2, "tools/list"), call(3, "decide", state="s", question="q", options=OPTS), call(4, "run_recipe", recipe="support-triage", input_path=DATA),
            call(5, "list_recipes"), call(6, "nope"), req(7, "bogus"), "{bad json", req(8, "ping")]
    _, raw, _ = rpc(*msgs, factory=make)
    lines = raw.splitlines()
    assert len(lines) == 9 and raw.endswith("\n")
    for line in lines:
        m = json.loads(line)
        assert m["jsonrpc"] == "2.0" and "id" in m and ("result" in m) != ("error" in m)
    cap = capsys.readouterr()
    assert cap.out == "" and "downloading the model..." in cap.err
    assert sys.stdout is not None and not sys.stdout.closed


def test_utf8_roundtrip():
    s = one(call(1, "decide", state="Où est mon colis ? 📦", question="Is it about delivery?"))["result"]["structuredContent"]
    assert s["answer"] == "yes"
