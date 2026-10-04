"""Parity with a recorded Decision Index run: argmax agreement on 50 sampled rows must be at least 99.5%.

DT_RESULTS  results.jsonl of the recorded run (required; the test is skipped without it)
DT_MODEL    Hub repo id or local model directory (default: decision-tune/decisiontune-1.0)
DT_BACKEND  auto, torch, onnx or mlx (default auto)
DT_DEVICE   torch device (default cpu)
"""
import json
import os
import random

import pytest

from decision_tune import REPO, DecisionModel
from decision_tune.engine import option_text

RESULTS = os.environ.get("DT_RESULTS")
N, SEED = 50, 20261004


def sample_rows():
    ok = []
    with open(RESULTS) as f:
        for line in f:
            if '"status":"ok"' in line[:4000] or '"status": "ok"' in line[:4000]:
                ok.append(line)
    random.Random(SEED).shuffle(ok)
    return [json.loads(line) for line in ok[:N]]


def predict(m, payload):
    out = {}
    for k, q in payload["questions"].items():
        if q["type"] == "choice":
            crit = q["criteria"]
            p = m._softmax(m.logits(payload["state"], q.get("instructions", ""), [option_text(a, d) for a, d in crit.items()]))
            probs = dict(zip(crit, p))
            out[k] = {"choice": max(probs, key=probs.get), "probs": probs}
        else:
            p = m._softmax(m.logits(payload["state"], q.get("instructions", ""), ["yes", "no"]))[0]
            out[k] = {"choice": p >= 0.5, "probs": {"yes": p}}
    return out


@pytest.mark.skipif(not (RESULTS and os.path.exists(RESULTS)), reason="DT_RESULTS not set")
def test_parity_with_recorded_run():
    m = DecisionModel.from_pretrained(os.environ.get("DT_MODEL", REPO), backend=os.environ.get("DT_BACKEND", "auto"),
                                      device=os.environ.get("DT_DEVICE", "cpu"), yes=True)
    rows, ok, qs, qok, maxd, bad = sample_rows(), 0, 0, 0, 0.0, []
    for r in rows:
        got, rec = predict(m, r["payload"]), r["response"]["answers"]
        assert set(got) == set(rec)
        row_ok = True
        for k, g in got.items():
            qs += 1
            if rec[k]["type"] == "choice":
                same = g["choice"] == rec[k]["choice"]
                d = max(abs(g["probs"][a] - rec[k]["probabilities"][a]) for a in g["probs"])
            else:
                same = g["choice"] == (rec[k]["noul"] >= 0.5)
                d = abs(g["probs"]["yes"] - rec[k]["noul"])
            maxd = max(maxd, d)
            qok += same
            row_ok &= same
        ok += row_ok
        if not row_ok:
            bad.append(r["run_id"])
    print(f"\nbackend {m.backend_name}: rows {ok}/{len(rows)} = {100 * ok / len(rows):.2f}%  questions {qok}/{qs}  max |dp| {maxd:.2e}  mismatches {bad}")
    assert ok / len(rows) >= 0.995
