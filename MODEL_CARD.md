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

<p align="center"><img src="https://img.shields.io/badge/Decision%20Index%200.2.1-29.57-da3200?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="Decision Index 0.2.1: 29.57"> <img src="https://img.shields.io/badge/size-395M-151b24?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="395M parameters"> <img src="https://img.shields.io/badge/license-Apache--2.0-151b24?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="Apache-2.0"> <img src="https://img.shields.io/badge/runs%20on-PyTorch%20%7C%20MLX%20%7C%20ONNX-4f5661?style=flat-square" style="display:inline-block;vertical-align:middle;margin:2px" alt="PyTorch, MLX, ONNX"></p>

<p align="center"><b><a href="https://decisiontune.com">Website</a> &nbsp;•&nbsp; <a href="https://huggingface.co/spaces/decision-tune/demo">Demo</a> &nbsp;•&nbsp; <a href="https://github.com/decision-tune/decision-tune">GitHub</a> &nbsp;•&nbsp; <a href="https://pypi.org/project/decision-tune/">PyPI</a></b></p>

# DecisionTune 1.0

DecisionTune 1.0 is a 395M decision model. You give it a state, a question and a list of options. It selects one option and gives a probability for each option. You can also ask a yes/no question. Then it gives P(yes). It does one encoder pass and does not generate text. It scores 29.57 on Decision Index 0.2.1. We measured all numbers on our runs. Estimates have a label.

**Key features**

- **29.57 on Decision Index 0.2.1.** This is from one complete run. A second seed scored 29.13.
- **Select an option, or get P(yes).** You get a probability for each option from one encoder pass. It does not generate text.
- **Fast on a laptop.** The median time for one request in our index run is 25.9 ms. You do not need a GPU server.
- **Runs on your computer.** Use PyTorch, MLX on Apple silicon, or ONNX. The weights are under Apache-2.0.

<p align="center"><img src="https://huggingface.co/decision-tune/decisiontune-1.0/resolve/main/assets/example-decision.png" alt="Real output of DecisionTune 1.0: for the state 'The order arrived broken.' it picks Shipping at 71.2 percent, over Tech support at 20.9 and Billing at 7.9. Recorded on a laptop CPU in 361 ms." width="680"></p>

Weights: Apache-2.0. Package: [`decision-tune`](https://pypi.org/project/decision-tune/) ([source](https://github.com/decision-tune/decision-tune)). Demo: [decision-tune/demo](https://huggingface.co/spaces/decision-tune/demo). Site: [decisiontune.com](https://decisiontune.com).

## How to use

> [!TIP]
> Describe your options. Short descriptions ("Shipping: delivery, lost or damaged packages") give much better results than labels only ("shipping").

The package is small. Before the first download of the weights (1.58 GB), it asks for your approval. It keeps the files in your Hugging Face cache. Then it checks each file against `manifest.json`.

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
# P(yes): here, a float near 1
```

**Command line** (with `uvx`, you do not need to install)

```bash
uvx decision-tune ask "Is the customer asking for a refund?" --state "The order arrived broken. I want my money back."
decision-tune ask "Which team handles this?" --state "I was charged twice this month." --option billing --option shipping --option "tech support"
decision-tune download --yes     # download without a prompt, for scripts and containers
```

**Local HTTP endpoint**

```bash
decision-tune serve              # http://127.0.0.1:8000/decide
curl -s http://127.0.0.1:8000/decide -H 'Content-Type: application/json' -d '{"state": "What is the weather tomorrow in Paris?", "question": "Which tool should be called?", "options": ["get_weather", "send_email", "create_calendar_event"]}'
curl -s http://127.0.0.1:8000/decide -H 'Content-Type: application/json' -d '{"state": "The order arrived broken. I want my money back.", "question": "Is the customer asking for a refund?"}'
```

**Local app.** Both command names work: `decision-tune` and `decisiontune`.

```bash
curl -LsSf https://decisiontune.com/install.sh | sh      # or: uv tool install "decision-tune[mlx]" on a Mac
decisiontune app                                         # opens the app in your browser
```

The app runs on your computer. Nothing leaves it. On a Mac with MLX, a short decision takes about 10 ms. If port 8000 is busy, the app tries the next 10 ports.

**Recipes (many items at once).** A recipe is a JSON file with the columns to read and the questions to ask. `support-triage` is built in. Each result row gets `needs_review` when a confidence is below `review_below`. The `.csv` and `.xlsx` output puts an apostrophe before any cell that starts with `=`, `+`, `-` or `@`.

```bash
decisiontune run support-triage tickets.csv      # writes tickets-decided.csv; use -o out.xlsx for Excel
decisiontune recipes                             # list the recipes
decisiontune recipe new my-recipe                # save a copy to edit
```

```python
from decision_tune import Recipe

results = Recipe.load("support-triage").run([{"subject": "Broken mug", "message": "The order arrived broken."}])
```

Over HTTP, POST rows to `/recipes/support-triage/run` with `Content-Type: application/json`.

**MCP (Claude Desktop, Cursor and others).** Run `decisiontune download` once. Then add this to your client settings:

```json
{"mcpServers": {"decisiontune": {"command": "decisiontune", "args": ["mcp"]}}}
```

The server has three tools: `decide`, `run_recipe` and `list_recipes`. It runs on your computer.

**Backends.** PyTorch is the default. On Apple silicon, `pip install "decision-tune[mlx]"` adds an MLX backend. The package selects it automatically. The MLX backend uses laya-mlx (Apache-2.0). `pip install "decision-tune[onnx]"` adds onnxruntime. To use it, set `--backend onnx` or `backend="onnx"`. All three backends read the files in this repo. They give the same answers on our parity rows.

**Python without the package.** `engine.py` in this repo is all of the inference code.

```bash
pip install torch transformers tokenizers safetensors huggingface_hub
hf download decision-tune/decisiontune-1.0 --exclude "onnx/*" --local-dir decisiontune-1.0
cd decisiontune-1.0
python -c 'from engine import DecisionModel; print(DecisionModel(".").yes_no("The order arrived broken. I want my money back.", "Is the customer asking for a refund?"))'
```

## How it works

<p align="center"><img src="https://huggingface.co/decision-tune/decisiontune-1.0/resolve/main/assets/how-it-works.png" alt="You supply a state, a question and options. DecisionTune 1.0 does one forward pass. You receive the selected option and a probability for each option, or P(yes)." width="100%"></p>

The model is ModernBERT-large and a 4 KB scoring head. Each question is one sequence:

`[CLS] question [SEP] [MASK] option_1 [MASK] option_2 ... [SEP] state [SEP]`

The head gives a score to the hidden state at each `[MASK]`. The probabilities are the softmax of these scores. A yes/no question uses the options `yes` and `no`. There is no calibration temperature and no option filter. The context limit is 8,192 tokens. The model refuses a longer sequence. It does not truncate it. A state can be a string or a JSON object. The model shows an object as `key: value | key: value`.

## Results

Decision Index 0.2.1, one full run: 29.57 (raw 46.80). The run had 150,317 requests that the index can score. 150,309 were ok and 8 were not supported. The context limit is 8,192 tokens.

| Area (skill) | DecisionTune 1.0 | DecisionTune 0.9 Preview | Change |
|---|---:|---:|---:|
| Knowledge & Reasoning | **13.3** | 12.2 | +1.1 |
| Language Understanding | **31.5** | 29.0 | +2.5 |
| Retrieval & Classification | **45.0** | 44.8 | +0.2 |
| Tools & Automation | **46.5** | 28.1 | +18.4 |
| Arts & Human Taste | **4.8** | 3.7 | +1.1 |

Bold: the best number in each row.

<p align="center"><img src="https://huggingface.co/decision-tune/decisiontune-1.0/resolve/main/assets/chart_b_areas.svg" alt="Per area skill, DecisionTune 1.0 against DecisionTune 0.9 Preview. Tools and Automation rises from 28.1 to 46.5." width="100%"></p>

DecisionTune 0.9 Preview is our earlier clean model. It scores 25.12. Nine benchmarks score 0.0. Examples are ANLI, GPQA Diamond, ChessBench and HLE.

**JevBench.** We ran its public set of 231 tasks on our computer. DecisionTune 1.0 answers 55.0% of them correctly (127 of 231, 95% CI 48.1 to 61.5). These results are for the public set only. They are not a JevBench board score.

**Speed.** In the index run, the median time for one request was 25.9 ms, and p95 was 407.8 ms (our laptop). Speed on your hardware can be different.

**Parity.** Torch and MLX give the same answer on 99.85% of 2,755 questions. Before each release, the package runs 50 recorded index rows again on each backend. PyTorch, ONNX and MLX each give the recorded answer on all 50.

## Intended use

Use it to route, sort and classify short English states with a small number of options. Use it to select tools. Use it for yes/no checks when you want a probability. It is small, and it runs on a laptop CPU.

## Limits

- It is not a general reasoning model. It gives scores to options. It does not explain or calculate. Its scores for knowledge, math, and arts and taste are low.
- English only.
- Most training units are shorter than 4,096 tokens. We removed units longer than 4,096 tokens. In stage 1, we cut states to 256 tokens. Inputs up to 8,192 tokens run, but we tested longer inputs less.
- Probabilities are a softmax of the option scores, without calibration. Use confidence to rank options, not as a true probability.
- The model compares the options with each other. If you add, remove or move options, the result can change.
- Short descriptions of the options give much better results than one-word labels. Vague questions with no criteria (for example, "Is this urgent?") give weak results. Write down the criteria.
- Do not use it for high-risk decisions about people (hiring, credit, housing, health, legal) without independent validation. Also do all reviews that the law requires.

## Training

The weights have three stages:

1. **Base.** answerdotai/ModernBERT-large (Apache-2.0).
2. **Stage 1.** One epoch on 17,000 items: 10,000 synthetic typed decisions (tasksource/procedural-typed-decisions) and 1,000 items each from Circa, CondaQA, GoEmotions, civil_comments, PubMedQA, Pavlick formality scores and QMSum. In this stage, we cut states to 256 tokens.
3. **Stage 2.** Two epochs on 16 training sets: BANKING77, CLINC150, GSM8K (code made the wrong options), WinoGrande, ContractNLI, Amazon ESCI, twitter-financial-news-sentiment, four tool-use sets (BFCL-style, API-Bank-style, ToolRet-style and When2Call-style), HellaSwag, a CLadder-style set from our generator, a HoVer-style set that we made from 2WikiMultihopQA, the RAGTruth train split, and the stage 1 items again at weight 2.5.

We made the tool-use sets, with the When2Call-style rows, from Glaive, ToolACE, Hermes and Funcdex tool dialogs. We did not use NVIDIA When2Call or xLAM data in training. A larger open model, Clef-Flash (Cloudflare/clef-flash, Apache-2.0), taught the model during part of its training. We used its soft labels on 8 datasets (distillation weight 0.5, temperature 1.0). The training run for DecisionTune 1.0 cost $1.69 on a rented A100.

## Training data and licenses

We fine-tuned DecisionTune 1.0 from answerdotai/ModernBERT-large (Apache-2.0). We trained it only on data that has a permissive license tag from its publisher (Apache-2.0, MIT, CC BY, CC0). We did not use datasets with non-commercial, share-alike or research-only terms. We did not audit the rights of the original text inside the tagged datasets.

Sources: BANKING77 (CC BY 4.0), CLINC150 (CC BY 3.0), GSM8K (MIT), WinoGrande (CC BY), ContractNLI (CC BY 4.0), Amazon ESCI (Apache-2.0), twitter-financial-news-sentiment (MIT), HellaSwag (MIT), RAGTruth train split (MIT), 2WikiMultihopQA (Apache-2.0), glaive-function-calling-v2 (Apache-2.0), ToolACE (Apache-2.0), hermes-function-calling-v1 (Apache-2.0), Funcdex-MT (MIT), procedural-typed-decisions (Apache-2.0), Circa (CC BY 4.0), CondaQA (Apache-2.0), GoEmotions (Apache-2.0), civil_comments (CC0), PubMedQA (MIT), Pavlick formality scores (CC BY 3.0), QMSum (MIT). We made the tool-use, HoVer-style and CLadder-style training sets from these sources and from the CLadder generator code (MIT). We did not use published CLadder rows. Part of the training used soft labels from Clef-Flash (Cloudflare/clef-flash, Apache-2.0).

**Known issues.** (1) Some sources contain text from other origins. We did not examine the terms of these origins: Wikipedia passages (CondaQA, 2WikiMultihopQA), Reddit comments (GoEmotions), tweets (twitter-financial-news-sentiment), PubMed abstracts (PubMedQA), parliamentary transcripts (QMSum), and web-found sentences and contracts (Pavlick formality, ContractNLI). (2) LLMs made some sources. Some of these LLMs have no name or belong to third parties. RAGTruth responses come from GPT-3.5, GPT-4, Llama-2 and Mistral models. LLM pipelines made the tool-call sets. We used the license tag of each publisher. We did not find the terms of the LLMs. (3) The MIT license link of HellaSwag goes to a source repository that is not available now. (4) CC BY sources require attribution: BANKING77 (PolyAI), CLINC150 (Larson et al.), WinoGrande (Allen Institute for AI), ContractNLI (Koreeda and Manning), Circa (Google Research), Pavlick formality scores (Pavlick and Tetreault). This text describes our data policy. It is not legal advice. Nobody did a combined legal review of the release.

## Disclosures

- **Test-informed plan.** We ran the full index eight times during development. We designed some fixes after we saw index results. We used no index rows, labels or option texts as training data.
- **Contamination audit.** Our overlap check removed 0 of 270,624 training rows. Notes: 47 rows overlap our practice set, not the index. ContractNLI rows have standard text that is also in test contracts (max Jaccard 0.482, below the 0.5 limit). We did not scan one older data pool again.
- **GSM8K.** In the index, the correct GSM8K answer is always the center option. If we set GSM8K to the score of our starting model, DecisionTune 1.0 scores 28.48. We report both numbers.
- **Two seeds.** We trained a second model with a different random seed. It scored 29.13 (raw 45.96). The two scores differ by 0.44 points.

## Verify your download

`manifest.json` lists the SHA-256 of each model file. The `decision-tune` package contains the SHA-256 of `manifest.json`. It does not load a file that does not match. The GitHub Actions release workflow also signs the manifest with Sigstore (keyless). The bundle is attached to the [v1.0.0 release](https://github.com/decision-tune/decision-tune/releases/tag/v1.0.0):

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
