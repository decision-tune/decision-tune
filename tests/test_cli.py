import csv
import errno
import json
import os
import shutil

import pytest

from decision_tune import cli, mcp, server
from decision_tune.hub import DecisionModel

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tickets.csv")


class Fake:
    """Scripted answers: choose picks 'shipping' (0.8), yes_no says 0.9, so nothing needs review."""

    def choose(self, state, question, options):
        keys = list(options)
        probs = {k: 0.1 for k in keys}
        probs["shipping" if "shipping" in keys else keys[0]] = 0.8
        best = max(probs, key=probs.get)
        return {"choice": best, "probabilities": probs, "confidence": probs[best]}

    def yes_no(self, state, question):
        return 0.9


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_TUNE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DECISION_TUNE_YES", raising=False)
    calls = []

    def fake_load(cls, repo_or_dir="x", **kw):
        calls.append(("from_pretrained", repo_or_dir, kw))
        return Fake()

    monkeypatch.setattr(DecisionModel, "from_pretrained", classmethod(fake_load))
    monkeypatch.setattr(server.webbrowser, "open", lambda url: calls.append(("browser", url)))
    return calls


def fake_serve(calls, fail_ports=()):
    def serve(model, host="127.0.0.1", port=8000, explicit_host=False, open_browser=False):
        calls.append(("serve", model, host, port, explicit_host, open_browser))
        if port in fail_ports:
            raise OSError(errno.EADDRINUSE, "Address already in use")
        if open_browser:
            server.webbrowser.open(f"http://{host}:{port}/")
    return serve


def test_ask_output_unchanged(env, capsys):
    cli.main(["ask", "Which team?", "--option", "billing", "--option", "shipping"])
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "shipping  (confidence 0.800)"
    assert out[1] == "  0.800  shipping" and out[2].startswith("  0.100  ")
    cli.main(["ask", "Refund?"])
    assert capsys.readouterr().out == "yes  (P(yes) = 0.9000)\n"
    cli.main(["ask", "Refund?", "--json"])
    assert json.loads(capsys.readouterr().out)["answer"] == "yes"


def test_serve_wiring(env, monkeypatch):
    monkeypatch.setattr(server, "serve", fake_serve(env))
    cli.main(["serve"])
    cli.main(["serve", "--host", "0.0.0.0", "--port", "9000"])
    s = [c for c in env if c[0] == "serve"]
    assert s[0][2:] == ("127.0.0.1", 8000, False, False)
    assert s[1][2:] == ("0.0.0.0", 9000, True, False)


def test_app_opens_browser_after_model_load(env, monkeypatch):
    monkeypatch.setattr(server, "serve", fake_serve(env))
    cli.main(["app", "--port", "8123"])
    names = [c[0] for c in env]
    assert names.index("from_pretrained") < names.index("browser")
    serve = next(c for c in env if c[0] == "serve")
    assert isinstance(serve[1], Fake) and serve[3] == 8123 and serve[5] is True
    assert next(c for c in env if c[0] == "browser")[1] == "http://127.0.0.1:8123/"


def test_app_no_browser_and_busy_port(env, monkeypatch, capsys):
    monkeypatch.setattr(server, "serve", fake_serve(env, fail_ports=(8000, 8001)))
    cli.main(["app", "--no-browser"])
    assert [c[3] for c in env if c[0] == "serve"] == [8000, 8001, 8002]
    assert not any(c[0] == "browser" for c in env) and all(c[5] is False for c in env if c[0] == "serve")
    assert "port 8000 is busy, using 8001" in capsys.readouterr().err
    monkeypatch.setattr(server, "serve", fake_serve(env, fail_ports=range(8000, 8011)))
    with pytest.raises(SystemExit, match="all busy"):
        cli.main(["app", "--no-browser"])


def test_run_writes_default_output_and_refuses_overwrite(env, tmp_path, capsys):
    src = tmp_path / "tickets.csv"
    shutil.copy(DATA, src)
    cli.main(["run", "support-triage", str(src)])
    out = tmp_path / "tickets-decided.csv"
    line = capsys.readouterr().out
    assert line.startswith("8 rows, 0 need review, ") and line.endswith(f" ms. Wrote {out}\n")
    assert len(list(csv.DictReader(open(out, encoding="utf-8-sig")))) == 8
    before = out.read_bytes()
    with pytest.raises(SystemExit, match="already exists"):
        cli.main(["run", "support-triage", str(src)])
    assert out.read_bytes() == before
    cli.main(["run", "support-triage", str(src), "--force"])
    assert not list(tmp_path.glob(".*.tmp"))


def test_run_cli_uses_recipe_answers(env, tmp_path):
    out = tmp_path / "o.csv"
    cli.main(["run", "support-triage", DATA, "-o", str(out)])
    rows = list(csv.DictReader(open(out, encoding="utf-8-sig")))
    assert {r["team"] for r in rows} == {"shipping"} and {r["refund"] for r in rows} == {"yes"}
    assert {r["refund_p_yes"] for r in rows} == {"0.9"} and {r["team_confidence"] for r in rows} == {"0.8"}
    assert rows[0]["subject"] == "Order #4417" and rows[0]["needs_review"] == "False"


def test_run_xlsx_folder_and_errors(env, tmp_path):
    import openpyxl

    cli.main(["run", "support-triage", DATA, "-o", str(tmp_path / "o.xlsx")])
    assert openpyxl.load_workbook(tmp_path / "o.xlsx").active.max_row == 9
    d = tmp_path / "notes"
    d.mkdir()
    (d / "a.txt").write_text("hello")
    r = tmp_path / "r.json"
    r.write_text(json.dumps({"name": "t", "read": ["text"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it a greeting?"}]}))
    cli.main(["run", str(r), str(d)])
    assert (tmp_path / "notes-decided.csv").exists()
    with pytest.raises(SystemExit, match="no column 'subject'"):
        cli.main(["run", "support-triage", str(tmp_path / "notes-decided.csv")])
    with pytest.raises(SystemExit, match="no such input"):
        cli.main(["run", "support-triage", str(tmp_path / "nope.csv")])


def test_recipes_and_recipe_commands(env, tmp_path, capsys):
    cli.main(["recipes"])
    line = capsys.readouterr().out.splitlines()[0].split()
    assert line[:2] == ["support-triage", "built-in"] and line[2:] == ["team,", "refund,", "tone"]
    cli.main(["recipe", "show", "support-triage"])
    assert json.loads(capsys.readouterr().out)["name"] == "support-triage"
    cli.main(["recipe", "new", "mine"])
    path = capsys.readouterr().out.strip()
    assert path == str(tmp_path / "home" / "recipes" / "mine.json") and json.load(open(path))["name"] == "mine"
    cli.main(["recipes"])
    assert "mine" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="already exists"):
        cli.main(["recipe", "new", "mine"])
    with pytest.raises(SystemExit, match="must match"):
        cli.main(["recipe", "new", "../x"])


def test_mcp_wiring_and_consent(env, monkeypatch):
    seen = []
    monkeypatch.setattr(mcp, "run_stdio", lambda factory: seen.append(factory))
    monkeypatch.setattr(cli, "_cached", lambda model, backend: False)
    cli.main(["mcp"])
    with pytest.raises(RuntimeError, match="Run `decisiontune download` first."):
        seen[-1]()
    assert not env  # no download, no load
    cli.main(["mcp", "--yes"])
    assert isinstance(seen[-1](), Fake) and env[-1][2]["yes"] is True
    monkeypatch.setenv("DECISION_TUNE_YES", "1")
    cli.main(["mcp"])
    assert isinstance(seen[-1](), Fake)
    monkeypatch.delenv("DECISION_TUNE_YES")
    monkeypatch.setattr(cli, "_cached", lambda model, backend: True)
    cli.main(["mcp"])
    assert isinstance(seen[-1](), Fake)


def test_mcp_missing_model_is_a_tool_error(env, monkeypatch):
    import io

    monkeypatch.setattr(cli, "_cached", lambda model, backend: False)
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "decide", "arguments": {"state": "x", "question": "ok?"}}}
    out = io.StringIO()
    mcp.run_stdio(lambda: cli._mcp_model(type("A", (), {"yes": False, "model": "m", "backend": "auto"})()), io.StringIO(json.dumps(req) + "\n"), out)
    res = json.loads(out.getvalue())["result"]
    assert res["isError"] and "Run `decisiontune download` first." in res["content"][0]["text"]
