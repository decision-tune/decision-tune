import csv
import io
import json
import os

import pytest

from decision_tune import Recipe, list_recipes
from decision_tune.recipe import (read_rows, recipes_dir, rows_from_csv_text, rows_from_lines, safe_cell, write_csv,
                                  write_xlsx)

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tickets.csv")


def spec(**kw):
    d = {"name": "t", "read": ["a"], "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}
    d.update(kw)
    return d


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_TUNE_HOME", str(tmp_path))
    return tmp_path


class Fake:
    """Records every call; answers are scripted per question text."""

    def __init__(self, choose=None, yes=None):
        self.calls, self._c, self._y = [], choose or {}, yes or {}

    def choose(self, state, question, options):
        self.calls.append(("choose", state, question, options))
        return self._c(len(self.calls)) if callable(self._c) else self._c[question]

    def yes_no(self, state, question):
        self.calls.append(("yes_no", state, question))
        return self._y(len(self.calls)) if callable(self._y) else self._y[question]


def res(choice, conf):
    return {"choice": choice, "probabilities": {choice: conf}, "confidence": conf}


# 1. validation, load, save, list, readers, writers
@pytest.mark.parametrize("bad,msg", [
    (spec(name="Bad Name"), "name"),
    (spec(name="../x"), "name"),
    (spec(questions=[{"name": "q", "type": "rank", "question": "?"}]), "type"),
    (spec(questions=[{"name": "q", "type": "choose", "question": "?", "options": ["only"]}]), "options"),
    (spec(questions=[{"name": "q", "type": "scale", "question": "?", "levels": {}}]), "levels"),
    (spec(questions=[]), "questions"),
    (spec(questions=[{"name": "q", "type": "yes_no", "question": "?"}] * 2), "unique"),
    (spec(review_below=1.5), "review_below"),
])
def test_validation_errors(bad, msg):
    with pytest.raises(ValueError, match=msg):
        Recipe.from_dict(bad)


def test_load_builtin_and_path(home, tmp_path):
    r = Recipe.load("support-triage")
    assert [q["name"] for q in r.questions] == ["team", "refund", "tone"] and r.review_below == 0.6
    p = tmp_path / "mine.json"
    p.write_text(json.dumps(spec(name="mine")))
    assert Recipe.load(str(p)).name == "mine"
    with pytest.raises(ValueError, match="support-triage"):
        Recipe.load("nope")
    with pytest.raises(ValueError):
        Recipe.load("../x")


def test_save_and_list_user_shadows_builtin(home):
    assert recipes_dir() == str(home / "recipes")
    assert {(x["name"], x["source"]) for x in list_recipes()} == {("support-triage", "built-in")}
    d = Recipe.from_dict(spec(name="support-triage", review_below=0.9)).save()
    assert d == str(home / "recipes" / "support-triage.json")
    Recipe.from_dict(spec(name="other")).save()
    got = {x["name"]: x for x in list_recipes()}
    assert got["support-triage"]["source"] == "user" and got["support-triage"]["questions"] == ["q"]
    assert got["other"]["source"] == "user" and len(got) == 2
    assert Recipe.load("support-triage").review_below == 0.9
    assert Recipe.from_dict(Recipe.load("other").to_dict()).to_dict() == Recipe.load("other").to_dict()


def test_read_rows_csv_xlsx_folder(tmp_path):
    cols, rows = read_rows(DATA)
    assert cols == ["subject", "message"] and len(rows) == 8 and rows[4]["message"].startswith("Thanks, the")
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.append(["a", "b"])
    wb.active.append(["x", 1])
    wb.save(tmp_path / "t.xlsx")
    cols, rows = read_rows(str(tmp_path / "t.xlsx"))
    assert cols == ["a", "b"] and rows == [{"a": "x", "b": 1}]
    d = tmp_path / "notes"
    d.mkdir()
    (d / "b.md").write_text("two")
    (d / "a.txt").write_text("one")
    (d / "skip.pdf").write_text("no")
    assert read_rows(str(d)) == (["file", "text"], [{"file": "a.txt", "text": "one"}, {"file": "b.md", "text": "two"}])
    with pytest.raises(ValueError):
        read_rows(str(d / "skip.pdf"))
    assert rows_from_lines("a\n\n b \n") == (["text"], [{"text": "a"}, {"text": "b"}])
    assert rows_from_csv_text("x,y\n1,2\n") == (["x", "y"], [{"x": "1", "y": "2"}])


def test_formula_guard_csv_and_xlsx(tmp_path):
    assert [safe_cell(v) for v in ("=1+1", "+1", "-1", "@a", "\tx", "\rx", "ok", 5)] == ["'=1+1", "'+1", "'-1", "'@a", "'\tx", "'\rx", "ok", 5]
    rows = [{"a": "=HYPERLINK(1)", "b": "fine"}]
    buf = io.StringIO()
    write_csv(buf, ["a", "b"], rows)
    assert list(csv.reader(io.StringIO(buf.getvalue()))) == [["a", "b"], ["'=HYPERLINK(1)", "fine"]]
    write_csv(str(tmp_path / "o.csv"), ["a", "b"], rows)
    assert (tmp_path / "o.csv").read_text().splitlines()[1].startswith("'=")
    openpyxl = pytest.importorskip("openpyxl")
    write_xlsx(str(tmp_path / "o.xlsx"), ["a", "b"], rows)
    ws = openpyxl.load_workbook(tmp_path / "o.xlsx").active
    assert [c.value for c in ws[2]] == ["'=HYPERLINK(1)", "fine"] and ws["A2"].data_type == "s"


# 2. PIN: the model is consulted for every question of every row
def test_run_calls_model_per_question_per_row():
    cols, rows = read_rows(DATA)
    r = Recipe.load("support-triage")
    q = {x["name"]: x["question"] for x in r.questions}
    fake = Fake(choose={q["team"]: res("billing", 0.91), q["tone"]: res("neu", 0.82)}, yes={q["refund"]: 0.97})
    seen = []
    out = r.run(rows, model=fake, progress=lambda i, n: seen.append((i, n)))
    assert len(out) == 8 and len(fake.calls) == 24
    assert seen[-1] == (8, 8)
    for i, row in enumerate(rows):
        assert fake.calls[3 * i][1] == {"subject": row["subject"], "message": row["message"]}
    assert fake.calls[0][3] == r.questions[0]["options"] and fake.calls[2][3] == r.questions[2]["levels"]
    o = out[0]
    assert o["subject"] == "Order #4417"
    assert (o["team"], o["team_confidence"]) == ("billing", 0.91)
    assert (o["refund"], o["refund_p_yes"], o["refund_confidence"]) == ("yes", 0.97, 0.97)
    assert (o["tone"], o["tone_confidence"]) == ("neu", 0.82) and o["needs_review"] is False
    assert r.output_columns(cols) == ["subject", "message", "team", "team_confidence", "refund", "refund_p_yes",
                                      "refund_confidence", "tone", "tone_confidence", "needs_review", "error"]


def test_single_read_column_passes_text_and_empty_read_passes_all():
    one = Recipe.from_dict(spec(read=["a"]))
    f = Fake(yes={"Is it?": 0.9})
    one.decide_row(f, {"a": "hello", "b": "no"})
    assert f.calls[0][1] == "hello"
    allc = Recipe.from_dict(spec(read=[]))
    allc.decide_row(f, {"a": "1", "b": "2"})
    assert f.calls[1][1] == {"a": "1", "b": "2"}


# 3. PIN: review threshold is strict "below"
def test_needs_review_threshold():
    r = Recipe.from_dict({"name": "t", "read": ["a"], "review_below": 0.6, "questions": [
        {"name": "c", "type": "choose", "question": "C?", "options": ["x", "y"]},
        {"name": "y", "type": "yes_no", "question": "Y?"}]})
    def go(conf, p):
        return r.decide_row(Fake(choose={"C?": res("x", conf)}, yes={"Y?": p}), {"a": "t"})
    assert go(0.59, 0.9)["needs_review"] is True
    assert go(0.61, 0.9)["needs_review"] is False
    d = go(0.95, 0.45)
    assert d["needs_review"] is True and d["y"] == "no" and d["y_confidence"] == 0.55 and d["y_p_yes"] == 0.45


def test_model_error_flags_row_instead_of_crashing():
    class Boom(Fake):
        def yes_no(self, state, question):
            raise ValueError("sequence of 9000 tokens exceeds the 8192-token context limit")
    out = Recipe.from_dict(spec()).run([{"a": "x"}, {"a": "y"}], model=Boom())
    assert len(out) == 2 and out[0]["needs_review"] is True and "9000 tokens" in out[0]["error"]
