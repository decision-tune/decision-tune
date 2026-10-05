import csv
import errno
import json
import os
import shutil
import socket
import sys
import urllib.parse

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
        m = Fake()
        calls.append(("from_pretrained", repo_or_dir, kw, m))
        return m

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


def free_ports(n):
    """First of n consecutive loopback ports that are free right now."""
    for _ in range(100):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            p = probe.getsockname()[1]
        socks = [socket.socket() for _ in range(n)]
        try:
            for i, s in enumerate(socks):
                s.bind(("127.0.0.1", p + i))
            return p
        except OSError:
            pass
        finally:
            for s in socks:
                s.close()
    raise RuntimeError("no free port run")


@pytest.fixture
def real_bind(env, monkeypatch):
    """Real server.serve on real sockets. Only serve_forever is replaced (it returns at once); the browser stub must find the port accepting."""
    orig = server.make_server

    def make(model, host="127.0.0.1", port=8000, explicit_host=False):
        s = orig(model, host, port, explicit_host)  # a busy port raises EADDRINUSE here, as in production
        env.append(("bound", model, s.server_address[1]))
        s.serve_forever = lambda: None
        return s

    def browser(url):
        with socket.create_connection(("127.0.0.1", urllib.parse.urlparse(url).port), timeout=2):  # refused if nothing is listening
            env.append(("browser", url))

    monkeypatch.setattr(server, "make_server", make)
    monkeypatch.setattr(server.webbrowser, "open", browser)
    return env


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
    cli.main(["serve", "--host", "127.0.0.1"])  # loopback keeps the Host check (DNS rebinding)
    s = [c for c in env if c[0] == "serve"]
    assert s[0][2:] == ("127.0.0.1", 8000, False, False)
    assert s[1][2:] == ("0.0.0.0", 9000, True, False)
    assert s[2][2:] == ("127.0.0.1", 8000, False, False)
    for h in ("127.1", "LOCALHOST", "127.0.0.2", "::1"):  # every loopback spelling keeps the Host check
        cli.main(["serve", "--host", h])
        assert [c for c in env if c[0] == "serve"][-1][4] is False, h


def test_app_opens_browser_after_model_load_and_bind(real_bind):
    port = free_ports(1)
    cli.main(["app", "--port", str(port)])
    names = [c[0] for c in real_bind]
    assert names == ["from_pretrained", "bound", "browser"]
    loaded = real_bind[0][3]
    assert real_bind[1][1] is loaded and real_bind[1][2] == port  # the served model is the one that was loaded
    assert real_bind[2][1] == f"http://127.0.0.1:{port}/"


def test_app_browser_is_not_opened_when_nothing_is_listening(env, monkeypatch):
    def never_started(model, host="127.0.0.1", port=8000, explicit_host=False, open_browser=False):
        server.webbrowser.open(f"http://{host}:{port}/")  # what a serve that skipped the bind would do

    monkeypatch.setattr(server, "serve", never_started)
    port = free_ports(1)
    monkeypatch.setattr(server.webbrowser, "open", lambda url: socket.create_connection(("127.0.0.1", port), timeout=2).close())
    with pytest.raises(OSError):  # the pin: this fake fails the same readiness check the real-server test applies
        cli.main(["app", "--port", str(port)])


def test_app_busy_ports_announce_only_the_bound_one(real_bind, capsys):
    p = free_ports(3)
    held = [socket.socket() for _ in range(2)]
    try:
        for i, s in enumerate(held):
            s.bind(("127.0.0.1", p + i))
            s.listen()
        cli.main(["app", "--no-browser", "--port", str(p)])
    finally:
        for s in held:
            s.close()
    cap = capsys.readouterr()
    assert [c[2] for c in real_bind if c[0] == "bound"] == [p + 2]
    assert f"on http://127.0.0.1:{p + 2}/" in cap.out
    assert "using" not in cap.err and "busy" not in cap.err  # nothing claims a port before it is bound
    assert not any(c[0] == "browser" for c in real_bind)


def test_app_exhausted_ports(env, monkeypatch, capsys):
    monkeypatch.setattr(server, "serve", fake_serve(env, fail_ports=range(8000, 8011)))
    with pytest.raises(SystemExit, match="all busy"):
        cli.main(["app", "--no-browser"])
    assert [c[3] for c in env if c[0] == "serve"] == list(range(8000, 8011))
    assert "using" not in capsys.readouterr().err


def test_app_help_says_next_10_ports(capsys):
    with pytest.raises(SystemExit):
        cli.main(["app", "--help"])
    assert "tries the next 10 ports" in " ".join(capsys.readouterr().out.split())


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


def run_csv(tmp_path, *extra, out="o.csv"):
    cli.main(["run", "support-triage", DATA, "-o", str(tmp_path / out), *extra])


def test_run_folder_dot_and_dotdot_write_beside_the_folder(env, tmp_path, monkeypatch):
    d = tmp_path / "notes"
    (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_text("hello")
    (d / "sub" / "b.txt").write_text("hi")
    r = tmp_path / "r.json"
    r.write_text(json.dumps({"name": "t", "read": ["text"], "questions": [{"name": "q", "type": "yes_no", "question": "Greeting?"}]}))
    monkeypatch.chdir(d)
    cli.main(["run", str(r), "."])
    assert (tmp_path / "notes-decided.csv").exists() and not (d / "-decided.csv").exists() and not list(d.glob("*-decided.csv"))
    (tmp_path / "notes-decided.csv").unlink()
    (d / "a.txt").unlink()  # the .. run reads sub/ only
    monkeypatch.chdir(d / "sub")
    cli.main(["run", str(r), ".."])
    assert (tmp_path / "notes-decided.csv").exists() and not list((d / "sub").glob("*-decided.csv"))


def test_run_output_created_during_the_run_is_not_overwritten(env, tmp_path, monkeypatch):
    out = tmp_path / "o.csv"

    def late(self, state, question):
        out.write_text("someone else's file")  # appears after the early existence check, before publish
        return 0.9

    monkeypatch.setattr(Fake, "yes_no", late)
    with pytest.raises(SystemExit, match="already exists"):
        run_csv(tmp_path)
    assert out.read_text() == "someone else's file"
    assert not [p for p in tmp_path.iterdir() if p != out]  # our temp file is gone, nothing else was touched
    run_csv(tmp_path, "--force")
    assert out.read_text(encoding="utf-8-sig").startswith("subject")


def test_run_dangling_symlink_output_is_refused_unless_forced(env, tmp_path):
    target = tmp_path / "elsewhere.csv"
    out = tmp_path / "o.csv"
    out.symlink_to(target)
    with pytest.raises(SystemExit, match="already exists"):
        run_csv(tmp_path)
    assert out.is_symlink() and os.readlink(out) == str(target) and not target.exists()
    run_csv(tmp_path, "--force")
    assert not out.is_symlink() and out.is_file() and not target.exists()  # the link itself was replaced, its target never written


def test_run_leaves_other_files_named_like_a_temp_alone(env, tmp_path):
    keep = tmp_path / ".o.csv.tmp"
    keep.write_text("mine")
    run_csv(tmp_path)
    run_csv(tmp_path, "--force")
    assert keep.read_text() == "mine"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".o.csv.tmp", "o.csv"]


def test_run_without_hard_links_still_refuses_and_forces(env, tmp_path, monkeypatch):
    def no_link(*a, **k):
        raise OSError(errno.EPERM, "links not supported")

    monkeypatch.setattr(os, "link", no_link)
    run_csv(tmp_path)
    (tmp_path / "o.csv").write_text("keep")
    with pytest.raises(SystemExit, match="already exists"):
        run_csv(tmp_path)
    (tmp_path / "x.csv").symlink_to(tmp_path / "gone")
    with pytest.raises(SystemExit, match="already exists"):
        run_csv(tmp_path, out="x.csv")
    assert (tmp_path / "o.csv").read_text() == "keep"
    run_csv(tmp_path, "--force")
    assert (tmp_path / "o.csv").read_text(encoding="utf-8-sig").startswith("subject")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["o.csv", "x.csv"]


def test_run_output_keeps_normal_file_permissions(env, tmp_path):
    run_csv(tmp_path)
    mask = os.umask(0)
    os.umask(mask)
    assert (tmp_path / "o.csv").stat().st_mode & 0o777 == 0o666 & ~mask


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
    with pytest.raises(SystemExit, match="missing columns: subject, message"):
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
    monkeypatch.setattr(mcp, "run_stdio", lambda factory, roots=None: seen.append(factory))
    cli.main(["mcp"])
    assert isinstance(seen[-1](), Fake) and env[-1][2]["yes"] is None  # no consent given: the loader must not be told yes
    cli.main(["mcp", "--yes"])
    seen[-1]()
    assert env[-1][2]["yes"] is True
    monkeypatch.setenv("DECISION_TUNE_YES", "1")
    cli.main(["mcp"])
    seen[-1]()
    assert env[-1][2]["yes"] is True
    monkeypatch.setenv("DECISION_TUNE_YES", "0")
    cli.main(["mcp"])
    seen[-1]()
    assert env[-1][2]["yes"] is None


def test_mcp_uncached_model_without_consent_is_a_tool_error(tmp_path, monkeypatch):
    """The real loader, no fakes: nothing cached, stdin not a terminal, no consent -> no download, a tool error that names the fix."""
    import io

    monkeypatch.delenv("DECISION_TUNE_YES", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    import huggingface_hub

    def no_network(*a, **k):
        if not k.get("local_files_only"):
            raise AssertionError("tried to download without consent")
        raise FileNotFoundError("not cached")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", no_network)
    args = type("A", (), {"yes": False, "model": "decision-tune/not-cached-here", "backend": "auto"})()
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "decide", "arguments": {"state": "x", "question": "ok?"}}}
    out = io.StringIO()
    mcp.run_stdio(lambda: cli._mcp_model(args), io.StringIO(json.dumps(req) + "\n"), out)
    res = json.loads(out.getvalue())["result"]
    assert res["isError"] and "Run `decisiontune download` first." in res["content"][0]["text"]


# ---- GATE-fix1
def test_ask_validates_options_before_loading_the_model(env):
    for opts in (["a"], ["", "b"], ["a", "a"], [str(i) for i in range(33)]):
        with pytest.raises(SystemExit, match="options must be 2 to 32"):
            cli.main(["ask", "Which?", *[x for o in opts for x in ("--option", o)]])
    assert env == []  # the model was never asked for


def test_run_counts_failed_rows_by_execution_not_by_an_error_column(env, tmp_path, capsys, monkeypatch):
    src = tmp_path / "in.csv"
    src.write_text("subject,message,error\nhi,ok,source error text\nyo,boom,other\n")
    r = tmp_path / "r.json"
    r.write_text(json.dumps({"name": "t", "read": ["message"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}))
    cli.main(["run", str(r), str(src), "-o", str(tmp_path / "a.csv")])
    assert "failed" not in capsys.readouterr().err  # a source column named error is data, not a failure

    def yes_no(self, state, question):
        if state == "boom":
            raise RuntimeError("too long")
        return 0.9

    monkeypatch.setattr(Fake, "yes_no", yes_no)
    cli.main(["run", str(r), str(src), "-o", str(tmp_path / "b.csv")])
    assert "1 rows failed" in capsys.readouterr().err


def test_mcp_allow_wiring(env, monkeypatch, tmp_path):
    a, b, c = (tmp_path / n for n in "abc")
    for d in (a, b, c):
        d.mkdir()
    seen = []
    monkeypatch.setattr(mcp, "run_stdio", lambda factory, roots=None: seen.append(roots))
    monkeypatch.delenv("DECISION_TUNE_ROOTS", raising=False)
    cli.main(["mcp"])
    assert seen[-1] is None
    cli.main(["mcp", "--allow", str(a), "--allow", str(b)])
    assert seen[-1] == [os.path.realpath(a), os.path.realpath(b)]
    monkeypatch.setenv("DECISION_TUNE_ROOTS", str(c))
    cli.main(["mcp", "--allow", str(a)])
    assert seen[-1] == [os.path.realpath(c), os.path.realpath(a)]


def test_mcp_allow_missing_folder_is_a_one_line_error(env, monkeypatch, tmp_path):
    monkeypatch.setattr(mcp, "run_stdio", lambda factory, roots=None: None)
    with pytest.raises(SystemExit, match="does not exist"):
        cli.main(["mcp", "--allow", str(tmp_path / "nope")])
