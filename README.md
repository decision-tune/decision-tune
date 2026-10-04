<p align="center"><img src="https://decisiontune.com/brand/out/social-1280x640.png" alt="DecisionTune: fast decisions for your app, on your own machine. 29.57 on Decision Index 0.2.1." width="100%"></p>

<p align="center"><img src="https://img.shields.io/badge/Decision%20Index%200.2.1-29.57-da3200?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="Decision Index 0.2.1: 29.57"> <img src="https://img.shields.io/badge/size-395M-151b24?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="395M parameters"> <img src="https://img.shields.io/badge/license-Apache--2.0-151b24?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="Apache-2.0"> <img src="https://img.shields.io/badge/runs%20on-PyTorch%20%7C%20MLX%20%7C%20ONNX-4f5661?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="PyTorch, MLX, ONNX"></p>

<p align="center"><b><a href="https://decisiontune.com">Website</a> &nbsp;•&nbsp; <a href="https://huggingface.co/spaces/decision-tune/demo">Demo</a> &nbsp;•&nbsp; <a href="https://huggingface.co/decision-tune/decisiontune-1.0">Model card</a> &nbsp;•&nbsp; <a href="https://pypi.org/project/decision-tune/">PyPI</a></b></p>

# decision-tune

DecisionTune 1.0 is a 395M decision model. Give it a state, a question and options: it picks one and gives a probability for each. Or ask a yes/no question and get P(yes). One encoder pass, no text generation, runs on a laptop CPU. It scores 29.57 on Decision Index 0.2.1.

**Key features**

- **29.57 on Decision Index 0.2.1**, from one complete run. A second seed scored 29.13.
- **Pick an option, or get P(yes).** A probability for every option, from one encoder pass with no text generation.
- **Fast on a laptop.** 25.9 ms per request at the median in our index run, no GPU server.
- **Runs on your machine.** PyTorch, MLX on Apple silicon, or ONNX. Weights under Apache-2.0.

<p align="center"><img src="https://decisiontune.com/brand/out/example-decision.png" alt="Real output of DecisionTune 1.0: for the state 'The order arrived broken.' it picks Shipping at 71.2 percent, over Tech support at 20.9 and Billing at 7.9. Recorded on a laptop CPU in 361 ms." width="680"></p>

The package is small. On first use it asks `Download DecisionTune 1.0 (1.58 GB, Apache-2.0) from Hugging Face? [Y/n]`, caches the files in your Hugging Face cache, and checks every file against a SHA-256 manifest before it loads anything.

## Install

```bash
pip install decision-tune               # PyTorch backend (default)
pip install "decision-tune[mlx]"        # adds MLX on Apple silicon, selected automatically
pip install "decision-tune[onnx]"       # adds onnxruntime: --backend onnx
```

Docker (CPU):

```bash
docker run --rm -p 8000:8000 -v dt-cache:/root/.cache/huggingface ghcr.io/decision-tune/decision-tune serve --host 0.0.0.0 --yes
```

MLX backend powered by laya-mlx (Apache-2.0).

## Python

> [!TIP]
> Describe your options. Short descriptions ("Shipping: delivery, lost or damaged packages") work much better than bare labels ("shipping").

```python
from decision_tune import DecisionModel

m = DecisionModel.from_pretrained("decision-tune/decisiontune-1.0")
m.choose("The order arrived broken.", "Which team should handle this customer message?",
         {"billing": "Billing: charges, refunds, invoices",
          "shipping": "Shipping: delivery, lost or damaged packages",
          "tech": "Tech support: bugs, crashes, login problems"})
# {'choice': 'shipping', 'probabilities': {'billing': ..., 'shipping': ..., 'tech': ...}, 'confidence': ...}
m.yes_no("The order arrived broken. I want my money back.", "Is the customer asking for a refund?")
# P(yes), a float close to 1 here
```

Options can also be a dict of `{key: description}`; the description is what the model reads and the key is what you get back. A state can be a string or a JSON object.

**Tip: describe your options.** Short descriptions ("Shipping: delivery, lost or damaged packages") work much better than bare labels ("shipping"). Vague judgment questions with no criteria, such as "Is this urgent?", are a weak spot: spell out what counts.

## How it works

<p align="center"><img src="https://decisiontune.com/brand/out/how-it-works.png" alt="You give it a state, a question and options. DecisionTune 1.0 makes one forward pass. You get back the choice and a probability for every option, or P(yes)." width="100%"></p>

Full results, training data and limits: [model card](https://huggingface.co/decision-tune/decisiontune-1.0).

## Command line

```bash
uvx decision-tune ask "Is the customer asking for a refund?" --state "The order arrived broken. I want my money back."
decision-tune ask "Which team handles this?" --state "I was charged twice this month." --option billing --option shipping --option "tech support"
decision-tune download --yes     # non-interactive download (or set DECISION_TUNE_YES=1)
```

`--backend auto|torch|mlx|onnx` picks the backend (`auto` = MLX on Apple silicon when installed, else PyTorch). `--json` prints JSON.

## Local HTTP endpoint

```bash
decision-tune serve              # http://127.0.0.1:8000/decide
curl -s http://127.0.0.1:8000/decide -d '{"state": "What is the weather tomorrow in Paris?", "question": "Which tool should be called?", "options": ["get_weather", "send_email", "create_calendar_event"]}'
curl -s http://127.0.0.1:8000/decide -d '{"state": "The order arrived broken. I want my money back.", "question": "Is the customer asking for a refund?"}'
```

It answers one request at a time and listens on localhost only by default.

## Behavior

The rendering and readout are the ones used for the published scores: one sequence per question, `[CLS] question [SEP] [MASK] option ... [SEP] state [SEP]`, a linear head on each `[MASK]`, softmax over options. No calibration temperature, no option filtering, no truncation: a sequence over the 8,192-token context is refused. `src/decision_tune/engine.py` is the whole inference code and is also in the model repo as `engine.py`.

## Verify the model files

`manifest.json` (in this repo and in the model repo) lists the SHA-256 of every model file, and the package pins the SHA-256 of `manifest.json`. A mismatch stops the load. On each release, GitHub Actions signs `manifest.json` with Sigstore (keyless, GitHub OIDC) and attaches `manifest.json.sigstore.json` to the release. To check it yourself:

```bash
pip install sigstore
python -m sigstore verify github manifest.json --bundle manifest.json.sigstore.json \
  --cert-identity https://github.com/decision-tune/decision-tune/.github/workflows/release.yml@refs/tags/v1.0.0
```

With cosign: `cosign verify-blob manifest.json --bundle manifest.json.sigstore.json --new-bundle-format --certificate-identity https://github.com/decision-tune/decision-tune/.github/workflows/release.yml@refs/tags/v1.0.0 --certificate-oidc-issuer https://token.actions.githubusercontent.com`

## Tests

```bash
pip install ".[test]" && pytest -q
DT_RESULTS=/path/to/results.jsonl DT_BACKEND=torch pytest -q -s tests/test_parity.py   # needs a recorded index run
```

## Use responsibly

The model scores options; it does not reason step by step, and its probabilities are not calibrated. Do not use it for high-stakes decisions about people (hiring, credit, housing, health, legal) without independent validation and any review the law requires. Provided "as is" under Apache-2.0, without warranty.

## License

Code and weights: Apache-2.0. Training data licenses and flags are on the model card.
