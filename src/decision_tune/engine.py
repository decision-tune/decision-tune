"""DecisionTune 1.0 inference engine. Standalone: needs only the model files plus torch and transformers (backend "torch"),
onnxruntime (backend "onnx") or laya-mlx on Apple silicon (backend "mlx").

The rendering and readout are the ones used for the published Decision Index scores:
one sequence per question, [CLS] question [SEP] ([MASK] option)+ [SEP] state [SEP]. A linear head scores the hidden state
at each [MASK]; probabilities are the softmax over those scores. A yes/no question uses the options "yes" and "no" and
returns P(yes). No calibration temperature, no option filtering, no truncation: a sequence longer than the 8,192-token
context is refused.

    from engine import DecisionModel          # run from a directory that holds the model files
    m = DecisionModel(".")
    m.choose("Order arrived broken.", "Which team handles this?", ["billing", "shipping", "tech support"])
    m.yes_no("Order arrived broken.", "Is the customer asking for a refund?")
"""
import json
import math
import os
import sys

NAME = "DecisionTune 1.0"
SPECIAL = ("[MASK]", "[CLS]", "[SEP]", "[PAD]", "[UNK]")
FILES = {  # model files each backend reads, relative to the model directory
    "torch": ("config.json", "tokenizer.json", "model.safetensors", "head.safetensors"),
    "onnx": ("config.json", "tokenizer.json", "onnx/model.onnx"),
}


def clean(s):
    for t in SPECIAL:
        s = s.replace(t, " ")
    return s


def state_text(s):
    if s is None:
        return ""
    if isinstance(s, str):
        return s
    if isinstance(s, dict):  # "key: value | key: value"; non-string values as compact JSON
        return " | ".join(f"{k}: {v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, separators=(',', ':'))}" for k, v in s.items())
    return json.dumps(s, ensure_ascii=False, separators=(",", ":"))


def as_text(x):
    return x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def option_text(key, desc):
    if isinstance(desc, str) and desc.strip():
        return desc
    if desc is not None and desc != "":
        return as_text(desc)
    return str(key).replace("_", " ")


class TorchBackend:
    def __init__(self, path, device=None, dtype="float32"):
        import torch
        from safetensors.torch import load_file
        from transformers import ModernBertConfig, ModernBertModel

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")  # mps only by explicit device="mps"
        dt = getattr(torch, dtype)
        cfg = ModernBertConfig.from_pretrained(path)
        self.model = ModernBertModel(cfg)
        self.model.load_state_dict(load_file(f"{path}/model.safetensors"), strict=True)
        hw = load_file(f"{path}/head.safetensors")
        self.head = torch.nn.Linear(cfg.hidden_size, 1)
        self.head.load_state_dict({"weight": hw["head.weight"], "bias": hw["head.bias"]})
        self.model.to(self.device, dt).eval()
        self.head.to(self.device, dt).eval()

    def logits(self, ids, pos):
        torch = self.torch
        with torch.inference_mode():
            x = torch.tensor([ids], device=self.device)
            h = self.model(input_ids=x, attention_mask=torch.ones_like(x)).last_hidden_state
            return self.head(h[0, pos]).squeeze(-1).float().cpu().tolist()


class OnnxBackend:
    def __init__(self, path, device=None, dtype="float32"):
        import numpy as np
        import onnxruntime as ort

        self.np, self.device = np, "cpu"
        self.sess = ort.InferenceSession(f"{path}/onnx/model.onnx", providers=["CPUExecutionProvider"])

    def logits(self, ids, pos):
        np = self.np
        x = np.array([ids], dtype=np.int64)
        out = self.sess.run(["logits"], {"input_ids": x, "attention_mask": np.ones_like(x), "positions": np.array(pos, dtype=np.int64)})[0]
        return [float(v) for v in out]


class MlxBackend:
    """Apple silicon GPU via the ModernBERT encoder in laya-mlx (Apache-2.0), reading the same safetensors."""

    def __init__(self, path, device=None, dtype="float32"):
        import mlx.core as mx
        import numpy as np
        from laya_mlx.model import EncoderConfig, ModernBert
        from safetensors.numpy import load_file

        mx.set_default_device(mx.gpu)
        mx.set_cache_limit(4 * 2**30)  # an uncapped MLX cache grows without bound on long runs
        self.mx, self.np, self.device = mx, np, "mlx"
        with open(f"{path}/config.json") as f:
            self.model = ModernBert(EncoderConfig.from_dict(json.load(f)))
        self.model.load_weights([(k, mx.array(v)) for k, v in load_file(f"{path}/model.safetensors").items()], strict=True)
        mx.eval(self.model.parameters())
        hw = load_file(f"{path}/head.safetensors")
        self.W, self.b = mx.array(hw["head.weight"][0]), mx.array(hw["head.bias"][0])

    def logits(self, ids, pos):
        mx, np = self.mx, self.np
        x = mx.array(np.array([ids], dtype=np.int32))
        h = self.model(x, mx.ones_like(x)).astype(mx.float32)
        return np.array(h[0, mx.array(pos)] @ self.W + self.b).tolist()


BACKENDS = {"torch": TorchBackend, "onnx": OnnxBackend, "mlx": MlxBackend}
FILES["mlx"] = FILES["torch"]


def pick_backend(backend="auto"):
    """"auto" = mlx on Apple silicon when laya-mlx is installed, else torch."""
    if backend != "auto":
        return backend
    import platform
    import importlib.util

    if sys.platform == "darwin" and platform.machine() == "arm64" and importlib.util.find_spec("laya_mlx"):
        return "mlx"
    return "torch"


class DecisionModel:
    def __init__(self, path, backend="auto", device=None, dtype="float32"):
        from tokenizers import Tokenizer

        backend = pick_backend(backend)
        if backend not in BACKENDS:
            raise ValueError(f"backend must be one of {sorted(BACKENDS)}, not {backend!r}")
        self.path, self.backend_name = path, backend
        self.tok = Tokenizer.from_file(f"{path}/tokenizer.json")
        with open(f"{path}/config.json") as f:
            self.limit = json.load(f)["max_position_embeddings"]
        self.CLS, self.SEP, self.MARK = (self.tok.token_to_id(t) for t in ("[CLS]", "[SEP]", "[MASK]"))
        self.backend = BACKENDS[backend](path, device=device, dtype=dtype)
        self.device = self.backend.device

    @classmethod
    def from_pretrained(cls, path, **kw):
        """Local model directory. The decision_tune package version also downloads from the Hub and checks SHA-256s."""
        return cls(os.path.expanduser(path), **kw)

    def _t(self, s):
        return self.tok.encode(clean(s), add_special_tokens=False).ids

    def encode(self, state, instructions, opts):
        ids, pos = [self.CLS] + self._t(as_text(instructions)) + [self.SEP], []
        for o in opts:
            pos.append(len(ids))
            ids += [self.MARK] + self._t(o)
        ids += [self.SEP] + self._t(state_text(state)) + [self.SEP]
        if len(ids) > self.limit:
            raise ValueError(f"sequence of {len(ids)} tokens exceeds the {self.limit}-token context limit")
        return ids, pos

    def logits(self, state, instructions, opts):
        return self.backend.logits(*self.encode(state, instructions, opts))

    @staticmethod
    def _softmax(z):
        top = max(z)
        w = [math.exp(v - top) for v in z]
        tot = sum(w)
        return [v / tot for v in w]

    def choose(self, state, question, options):
        """options: a list of strings, or {key: description}. Returns {"choice", "probabilities", "confidence"}."""
        crit = options if isinstance(options, dict) else {o: o for o in options}
        if len(crit) < 2:
            raise ValueError("need at least 2 options")
        p = self._softmax(self.logits(state, question, [option_text(k, d) for k, d in crit.items()]))
        probs = dict(zip(crit, p))
        best = max(probs, key=probs.get)
        return {"choice": best, "probabilities": probs, "confidence": probs[best]}

    def yes_no(self, state, question):
        """P(yes) for a yes/no question."""
        return self._softmax(self.logits(state, question, ["yes", "no"]))[0]
