<p align="center"><img src="https://decisiontune.com/brand/out/social-1280x640.png" alt="DecisionTune: fast decisions for your app, on your own machine. 29.57 on Decision Index 0.2.1." width="100%"></p>

<p align="center"><img src="https://img.shields.io/badge/Decision%20Index%200.2.1-29.57-da3200?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="Decision Index 0.2.1: 29.57"> <img src="https://img.shields.io/badge/size-395M-151b24?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="395M parameters"> <img src="https://img.shields.io/badge/license-Apache--2.0-151b24?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="Apache-2.0"> <img src="https://img.shields.io/badge/runs%20on-PyTorch%20%7C%20MLX%20%7C%20ONNX-4f5661?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="PyTorch, MLX, ONNX"></p>

<p align="center"><b><a href="https://decisiontune.com">Website</a> &nbsp;•&nbsp; <a href="https://huggingface.co/spaces/decision-tune/demo">Demo</a> &nbsp;•&nbsp; <a href="https://huggingface.co/decision-tune/decisiontune-1.0">Model card</a> &nbsp;•&nbsp; <a href="https://pypi.org/project/decision-tune/">PyPI</a></b></p>

# decision-tune

DecisionTune 1.0 is a 395M decision model. You give it a state, a question and options. It selects one option and gives a probability for each option. You can also ask a yes/no question. Then it gives P(yes). It does one encoder pass and does not generate text. It runs on a laptop CPU. It scores 29.57 on Decision Index 0.2.1.

**Key features**

- **29.57 on Decision Index 0.2.1.** This is from one complete run. A second seed scored 29.13.
- **Select an option, or get P(yes).** You get a probability for each option from one encoder pass. It does not generate text.
- **Fast on a laptop.** The median time for one request in our index run is 25.9 ms. You do not need a GPU server.
- **Runs on your computer.** Use PyTorch, MLX on Apple silicon, or ONNX. The weights are under Apache-2.0.

<p align="center"><img src="https://decisiontune.com/brand/out/example-decision.png" alt="Real output of DecisionTune 1.0: for the state 'The order arrived broken.' it picks Shipping at 71.2 percent, over Tech support at 20.9 and Billing at 7.9. Recorded on a laptop CPU in 361 ms." width="680"></p>

The package is small. On first use, it asks `Download DecisionTune 1.0 (1.58 GB, Apache-2.0) from Hugging Face? [Y/n]`. It keeps the files in your Hugging Face cache. Before it loads a file, it checks the file against a SHA-256 manifest.

## Install

```bash
pip install decision-tune               # PyTorch backend (the default)
pip install "decision-tune[mlx]"        # adds MLX on Apple silicon; the package selects it automatically
pip install "decision-tune[onnx]"       # adds onnxruntime; use --backend onnx
```

Docker (CPU):

```bash
docker run --rm -p 8000:8000 -v dt-cache:/root/.cache/huggingface ghcr.io/decision-tune/decision-tune serve --host 0.0.0.0 --yes
```

The MLX backend uses laya-mlx (Apache-2.0).

## Python

> [!TIP]
> Describe your options. Short descriptions ("Shipping: delivery, lost or damaged packages") give much better results than labels only ("shipping").

```python
from decision_tune import DecisionModel

m = DecisionModel.from_pretrained("decision-tune/decisiontune-1.0")
m.choose("The order arrived broken.", "Which team should handle this customer message?",
         {"billing": "Billing: charges, refunds, invoices",
          "shipping": "Shipping: delivery, lost or damaged packages",
          "tech": "Tech support: bugs, crashes, login problems"})
# {'choice': 'shipping', 'probabilities': {'billing': ..., 'shipping': ..., 'tech': ...}, 'confidence': ...}
m.yes_no("The order arrived broken. I want my money back.", "Is the customer asking for a refund?")
# P(yes): here, a float near 1
```

Options can also be a dict of `{key: description}`. The model reads the description. You get the key back. A state can be a string or a JSON object.

**Tip: describe your options.** Short descriptions ("Shipping: delivery, lost or damaged packages") give much better results than labels only ("shipping"). Vague questions with no criteria, such as "Is this urgent?", give weak results. Write down the criteria.

## How it works

<p align="center"><img src="https://decisiontune.com/brand/out/how-it-works.png" alt="You supply a state, a question and options. DecisionTune 1.0 does one forward pass. You receive the selected option and a probability for each option, or P(yes)." width="100%"></p>

For the full results, training data and limits, see the [model card](https://huggingface.co/decision-tune/decisiontune-1.0).

## Command line

```bash
uvx decision-tune ask "Is the customer asking for a refund?" --state "The order arrived broken. I want my money back."
decision-tune ask "Which team handles this?" --state "I was charged twice this month." --option billing --option shipping --option "tech support"
decision-tune download --yes     # download without a prompt (or set DECISION_TUNE_YES=1)
```

`--backend auto|torch|mlx|onnx` selects the backend. `auto` selects MLX on Apple silicon if MLX is installed. If not, it selects PyTorch. `--json` prints JSON.

## Local HTTP endpoint

```bash
decision-tune serve              # http://127.0.0.1:8000/decide
curl -s http://127.0.0.1:8000/decide -d '{"state": "What is the weather tomorrow in Paris?", "question": "Which tool should be called?", "options": ["get_weather", "send_email", "create_calendar_event"]}'
curl -s http://127.0.0.1:8000/decide -d '{"state": "The order arrived broken. I want my money back.", "question": "Is the customer asking for a refund?"}'
```

It answers one request at a time. By default, it listens only on localhost.

## Behavior

The package uses the same input format and readout as the published scores. Each question is one sequence: `[CLS] question [SEP] [MASK] option ... [SEP] state [SEP]`. A linear head scores each `[MASK]`, and a softmax over the options gives the probabilities. There is no calibration temperature, no option filter and no truncation. The package refuses a sequence longer than 8,192 tokens. `src/decision_tune/engine.py` is all of the inference code. The model repo also contains it as `engine.py`.

## Verify the model files

`manifest.json` is in this repo and in the model repo. It lists the SHA-256 of each model file. The package contains the SHA-256 of `manifest.json`. If a hash does not match, the package stops the load. For each release, GitHub Actions signs `manifest.json` with Sigstore (keyless, GitHub OIDC). It attaches `manifest.json.sigstore.json` to the release. To do the check yourself:

```bash
pip install sigstore
python -m sigstore verify github manifest.json --bundle manifest.json.sigstore.json \
  --cert-identity https://github.com/decision-tune/decision-tune/.github/workflows/release.yml@refs/tags/v1.0.0
```

To use cosign: `cosign verify-blob manifest.json --bundle manifest.json.sigstore.json --new-bundle-format --certificate-identity https://github.com/decision-tune/decision-tune/.github/workflows/release.yml@refs/tags/v1.0.0 --certificate-oidc-issuer https://token.actions.githubusercontent.com`

## Tests

```bash
pip install ".[test]" && pytest -q
DT_RESULTS=/path/to/results.jsonl DT_BACKEND=torch pytest -q -s tests/test_parity.py   # this test needs a recorded index run
```

## Use responsibly

The model gives scores to options. It does not reason step by step. Its probabilities are not calibrated. Do not use it for high-risk decisions about people (hiring, credit, housing, health, legal) without independent validation. Also do all reviews that the law requires. We supply the model "as is" under Apache-2.0, without warranty.

## License

Code and weights: Apache-2.0. The model card shows the licenses and known issues of the training data.
