---
license: apache-2.0
base_model: answerdotai/ModernBERT-large
language: en
tags:
  - decision
  - classification
  - routing
  - tool-selection
  - modernbert
  - onnx
datasets:
  - PolyAI/banking77
  - clinc/clinc_oos
  - openai/gsm8k
  - allenai/winogrande
  - tasksource/esci
  - zeroshot/twitter-financial-news-sentiment
  - Rowan/hellaswag
  - xanhho/2WikiMultihopQA
  - glaiveai/glaive-function-calling-v2
  - Team-ACE/ToolACE
  - NousResearch/hermes-function-calling-v1
  - prem-research/Funcdex-MT-Function-Calling
  - tasksource/procedural-typed-decisions
  - google-research-datasets/circa
  - lasha-nlp/CONDAQA
  - google-research-datasets/go_emotions
  - google/civil_comments
  - qiaojin/PubMedQA
  - osyvokon/pavlick-formality-scores
---

<p align="center"><img src="https://huggingface.co/decision-tune/decisiontune-1.0/resolve/main/assets/social-1280x640.png" alt="DecisionTune: fast decisions for your app, on your own machine. 29.57 on Decision Index 0.2.1." width="100%"></p>

<p align="center"><img src="https://img.shields.io/badge/Decision%20Index%200.2.1-29.57-da3200?style=flat-square" alt="Decision Index 0.2.1: 29.57"> <img src="https://img.shields.io/badge/size-395M-151b24?style=flat-square" alt="395M parameters"> <img src="https://img.shields.io/badge/license-Apache--2.0-151b24?style=flat-square" alt="Apache-2.0"> <img src="https://img.shields.io/badge/runs%20on-PyTorch%20%7C%20MLX%20%7C%20ONNX-4f5661?style=flat-square" alt="PyTorch, MLX, ONNX"></p>

<p align="center"><b><a href="https://decisiontune.com">Website</a> &nbsp;•&nbsp; <a href="https://huggingface.co/spaces/decision-tune/demo">Demo</a> &nbsp;•&nbsp; <a href="https://github.com/decision-tune/decision-tune">GitHub</a> &nbsp;•&nbsp; <a href="https://pypi.org/project/decision-tune/">PyPI</a></b></p>

# DecisionTune 1.0

DecisionTune 1.0 is a 395M decision model. Give it a state, a question and a list of options: it picks one and gives a probability for each. Or ask a yes/no question and get P(yes). One encoder pass, no text generation. It scores 29.57 on Decision Index 0.2.1. All numbers are measured on our own runs unless marked as an estimate.

**Key features**

- **29.57 on Decision Index 0.2.1**, the highest score we can see under 500M parameters (board as of 2026-10-03).
- **Pick an option, or get P(yes).** A probability for every option, from one encoder pass with no text generation.
- **Fast on a laptop.** 25.9 ms per request at the median in our index run, no GPU server.
- **Runs on your machine.** PyTorch, MLX on Apple silicon, or ONNX. Weights under Apache-2.0.

<p align="center"><img src="https://huggingface.co/decision-tune/decisiontune-1.0/resolve/main/assets/example-decision.png" alt="Real output of DecisionTune 1.0: for the state 'The order arrived broken.' it picks Shipping at 71.2 percent, over Tech support at 20.9 and Billing at 7.9. Recorded on a laptop CPU in 361 ms." width="680"></p>

Weights: Apache-2.0. Package: [`decision-tune`](https://pypi.org/project/decision-tune/) ([source](https://github.com/decision-tune/decision-tune)). Demo: [decision-tune/demo](https://huggingface.co/spaces/decision-tune/demo). Site: [decisiontune.com](https://decisiontune.com).

## How to use

> [!TIP]
> Describe your options. Short descriptions ("Shipping: delivery, lost or damaged packages") work much better than bare labels ("shipping").

The package is small. On first use it asks before it downloads the weights (1.58 GB) into your Hugging Face cache, then checks every file against `manifest.json`.

**pip and Python**

```bash
pip install decision-tune
```

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

**Command line** (no install with `uvx`)

```bash
uvx decision-tune ask "Is the customer asking for a refund?" --state "The order arrived broken. I want my money back."
decision-tune ask "Which team handles this?" --state "I was charged twice this month." --option billing --option shipping --option "tech support"
decision-tune download --yes     # non-interactive download, for scripts and containers
```

**Local HTTP endpoint**

```bash
decision-tune serve              # http://127.0.0.1:8000/decide
curl -s http://127.0.0.1:8000/decide -d '{"state": "What is the weather tomorrow in Paris?", "question": "Which tool should be called?", "options": ["get_weather", "send_email", "create_calendar_event"]}'
curl -s http://127.0.0.1:8000/decide -d '{"state": "The order arrived broken. I want my money back.", "question": "Is the customer asking for a refund?"}'
```

**Backends.** PyTorch is the default. On Apple silicon, `pip install "decision-tune[mlx]"` adds an MLX backend, selected automatically (MLX backend powered by laya-mlx, Apache-2.0). `pip install "decision-tune[onnx]"` adds onnxruntime; use `--backend onnx` or `backend="onnx"`. All three read the files in this repo and give the same answers on our parity rows.

**Raw Python, without the package.** `engine.py` in this repo is the whole inference code.

```bash
pip install torch transformers tokenizers safetensors huggingface_hub
hf download decision-tune/decisiontune-1.0 --exclude "onnx/*" --local-dir decisiontune-1.0
cd decisiontune-1.0
python -c 'from engine import DecisionModel; print(DecisionModel(".").yes_no("The order arrived broken. I want my money back.", "Is the customer asking for a refund?"))'
```

## How it works

<p align="center"><img src="https://huggingface.co/decision-tune/decisiontune-1.0/resolve/main/assets/how-it-works.png" alt="You give it a state, a question and options. DecisionTune 1.0 makes one forward pass. You get back the choice and a probability for every option, or P(yes)." width="100%"></p>

ModernBERT-large plus a 4 KB scoring head. Each question becomes one sequence:

`[CLS] question [SEP] [MASK] option_1 [MASK] option_2 ... [SEP] state [SEP]`

The head scores the hidden state at every `[MASK]`. The probabilities are the softmax over those scores. A yes/no question uses the options `yes` and `no`. There is no calibration temperature and no option filtering. The context limit is 8,192 tokens: a longer sequence is refused, not truncated. A state can be a string or a JSON object; an object renders as `key: value | key: value`.

## Results

Decision Index 0.2.1, one full run: 29.57 (raw 46.80). 150,317 scoreable requests: 150,309 ok, 8 unsupported. The context limit is 8,192 tokens.

Under 500M parameters, the highest other entry we can see is Dinah-0 at 27.63 (150M, pending, not merged). DecisionTune 1.0 is 1.94 points above it. Board as of 2026-10-03.

<p align="center"><img src="https://huggingface.co/decision-tune/decisiontune-1.0/resolve/main/assets/chart_a_size_class.svg" alt="Decision Index by size for entrants up to 2B. DecisionTune 1.0 scores 29.57 at 395M parameters." width="100%"></p>

| Area (skill) | DecisionTune 1.0 | DecisionTune 0.9 Preview | Change | Dinah-0 |
|---|---:|---:|---:|---:|
| Knowledge & Reasoning | 13.3 | 12.2 | +1.1 | **19.1** |
| Language Understanding | **31.5** | 29.0 | +2.5 | 22.7 |
| Retrieval & Classification | 45.0 | 44.8 | +0.2 | **47.8** |
| Tools & Automation | **46.5** | 28.1 | +18.4 | 37.9 |
| Arts & Human Taste | **4.8** | 3.7 | +1.1 | 3.5 |

Bold: the best number in each row.

<p align="center"><img src="https://huggingface.co/decision-tune/decisiontune-1.0/resolve/main/assets/chart_b_areas.svg" alt="Per area skill, DecisionTune 1.0 against DecisionTune 0.9 Preview. Tools and Automation rises from 28.1 to 46.5." width="100%"></p>

DecisionTune 0.9 Preview is our earlier clean model; it scores 25.12. Nine benchmarks score 0.0, including ANLI, GPQA Diamond, ChessBench and HLE.

**JevBench.** We ran its public set of 231 tasks on our own machine. DecisionTune 1.0 answers 55.0% correctly (127 of 231, 95% CI 48.1 to 61.5). On JevBench, DecisionTune 1.0 is not at the top of its size class. Public results for models under 500M parameters range from 22.1% to 62.8%. At least seven of them are above DecisionTune 1.0. This is the public set only, not a JevBench board score.

**Speed.** In the index run: 25.9 ms per request, p95 407.8 ms (our laptop). Your hardware will differ.

**Parity.** Torch and MLX agree on 99.85% of answers over 2,755 questions. The package's release gate reruns 50 recorded index rows on each backend: PyTorch, ONNX and MLX each match the recorded answer on all 50.

## Intended use

Routing, triage and classification over short English states with a handful of options. Tool selection. Yes/no screening where you want a probability. It is small enough to run on a laptop CPU.

## Limits

- It is not a general reasoner. It scores options; it does not explain or compute. Knowledge, math and arts-and-taste scores are low.
- English only.
- Most training units are under 4,096 tokens (units over 4,096 were dropped); the stage 1 items have states cut to 256 tokens. Inputs up to 8,192 tokens run, but longer inputs are less tested.
- Probabilities are a softmax over option scores, with no calibration. Treat confidence as a ranking signal.
- Options are scored against each other. Adding, removing or reordering options can change the result.
- Short option descriptions work much better than bare one-word labels. Vague judgment questions with no criteria (for example "Is this urgent?") are a weak spot: spell out what counts.
- Do not use it for high-stakes decisions about people (hiring, credit, housing, health, legal) without independent validation and any review the law requires.

## Training

The weights went through three stages:

1. **Base.** answerdotai/ModernBERT-large (Apache-2.0).
2. **Stage 1.** One epoch on 17,000 items: 10,000 procedural synthetic typed decisions (tasksource/procedural-typed-decisions) plus 1,000 each from Circa, CondaQA, GoEmotions, civil_comments, PubMedQA, Pavlick formality scores and QMSum. States were cut to 256 tokens in this stage.
3. **Stage 2.** Two epochs on 16 training sets: BANKING77, CLINC150, GSM8K (distractors made by code), WinoGrande, ContractNLI, Amazon ESCI, twitter-financial-news-sentiment, four tool-use sets (BFCL-style, API-Bank-style, ToolRet-style and When2Call-style), HellaSwag, a CLadder-style set from our own generator, a HoVer-style set built from 2WikiMultihopQA, the RAGTruth train split, and a replay of the stage 1 items at weight 2.5.

The tool-use sets, including the When2Call-style rows, were built by us from Glaive, ToolACE, Hermes and Funcdex tool dialogs. No NVIDIA When2Call or xLAM data was used in training. Part of its training learned from a larger open model, Clef-Flash (Cloudflare/clef-flash, Apache-2.0): its soft labels were used on 8 datasets (distillation weight 0.5, temperature 1.0). Training DecisionTune 1.0 itself cost $1.69 on a rented A100.

## Training data and licenses

DecisionTune 1.0 is fine-tuned from answerdotai/ModernBERT-large (Apache-2.0). It was trained only on data released under permissive license tags by their publishers (Apache-2.0, MIT, CC BY, CC0). Datasets whose publishers state non-commercial, share-alike or research-only terms were excluded. Upstream text rights inside tagged datasets were not audited.

Sources: BANKING77 (CC BY 4.0), CLINC150 (CC BY 3.0), GSM8K (MIT), WinoGrande (CC BY), ContractNLI (CC BY 4.0), Amazon ESCI (Apache-2.0), twitter-financial-news-sentiment (MIT), HellaSwag (MIT), RAGTruth train split (MIT), 2WikiMultihopQA (Apache-2.0), glaive-function-calling-v2 (Apache-2.0), ToolACE (Apache-2.0), hermes-function-calling-v1 (Apache-2.0), Funcdex-MT (MIT), procedural-typed-decisions (Apache-2.0), Circa (CC BY 4.0), CondaQA (Apache-2.0), GoEmotions (Apache-2.0), civil_comments (CC0), PubMedQA (MIT), Pavlick formality scores (CC BY 3.0), QMSum (MIT). Tool-use, HoVer-style and CLadder-style training sets were built by us from these sources and from the MIT-licensed CLadder generator code; no published CLadder rows were used. Part of training used soft labels from Clef-Flash (Cloudflare/clef-flash, Apache-2.0).

**Known flags.** (1) Some sources contain text from other origins whose own terms we did not trace: Wikipedia passages (CondaQA, 2WikiMultihopQA), Reddit comments (GoEmotions), tweets (twitter-financial-news-sentiment), PubMed abstracts (PubMedQA), parliamentary transcripts (QMSum), and web-found sentences and contracts (Pavlick formality, ContractNLI). (2) Some sources are LLM-generated, with generators unnamed or third-party: RAGTruth responses come from GPT-3.5, GPT-4, Llama-2 and Mistral models; the tool-call sets were made by LLM pipelines. We relied on each publisher's license tag and did not establish the generators' terms. (3) HellaSwag's MIT license link points to a source repository that is currently not accessible. (4) CC BY sources require attribution: BANKING77 (PolyAI), CLINC150 (Larson et al.), WinoGrande (Allen Institute for AI), ContractNLI (Koreeda and Manning), Circa (Google Research), Pavlick formality scores (Pavlick and Tetreault). This is a description of our data policy, not legal advice; no combined legal review of the release has been done.

## Disclosures

- **Test-informed plan.** We ran the full index eight times during development. We designed several fixes after we saw index results. We used no index rows, labels or option texts as training data.
- **Contamination audit.** Our overlap check dropped 0 of 270,624 training rows. Caveats: 47 rows overlap our own practice set, not the index. ContractNLI rows share boilerplate with test contracts (max Jaccard 0.482, under the 0.5 line). One older data pool was not scanned again.
- **GSM8K.** In the index, the GSM8K gold answer is always the center option. With GSM8K at our starting model's value, DecisionTune 1.0 scores 28.48. We report both numbers.
- **Two seeds.** A second training run with a different random seed scored 29.13 (raw 45.96). The two seeds differ by 0.44 points, and both are above Dinah-0.

## Verify your download

`manifest.json` lists the SHA-256 of every model file. The `decision-tune` package pins the SHA-256 of `manifest.json` and refuses to load a file that does not match. The manifest is also signed with Sigstore (keyless, by the GitHub Actions release workflow) and the bundle is attached to the [v1.0.0 release](https://github.com/decision-tune/decision-tune/releases/tag/v1.0.0):

```bash
pip install sigstore
python -m sigstore verify github manifest.json --bundle manifest.json.sigstore.json \
  --cert-identity https://github.com/decision-tune/decision-tune/.github/workflows/release.yml@refs/tags/v1.0.0
```

## Citation

```bibtex
@misc{decisiontune2026,
  title        = {DecisionTune 1.0: a 395M decision model},
  author       = {{DecisionTune}},
  year         = {2026},
  howpublished = {\url{https://huggingface.co/decision-tune/decisiontune-1.0}},
  note         = {hotin.ai}
}
```
