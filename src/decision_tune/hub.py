"""Download from the Hugging Face Hub (asks first), then check every file against manifest.json before loading.

The package pins the SHA-256 of the 1.0 manifest, and the manifest pins every model file. The same manifest is signed with
Sigstore on the GitHub release (see README), so the chain is: signed manifest -> pinned hash -> file hashes.
"""
import hashlib
import json
import os
import sys

from .engine import FILES, pick_backend
from .engine import DecisionModel as _Engine

REPO = "decision-tune/decisiontune-1.0"
MANIFEST_SHA256 = "2ec93545ef3fcc2135e8b5c14e4b4690ff99535e9bc8ed5b85c98d071564a715"
PROMPT = "Download DecisionTune 1.0 (1.58 GB, Apache-2.0) from Hugging Face? [Y/n] "


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def verify(path, files, pinned=None):
    """Raise unless manifest.json (and its pinned hash, when given) and every listed file match. Returns the manifest hash."""
    mpath = os.path.join(path, "manifest.json")
    if not os.path.isfile(mpath):
        raise ValueError(f"no manifest.json in {path}: refusing to load unverified model files")
    got = sha256(mpath)
    if pinned and got != pinned:
        raise ValueError(f"manifest.json SHA-256 is {got}, expected {pinned}: refusing to load")
    with open(mpath) as f:
        manifest = json.load(f)["files"]
    for name in files:
        if name not in manifest:
            raise ValueError(f"{name} is not in manifest.json: refusing to load")
        p = os.path.join(path, name)
        if not os.path.isfile(p) or sha256(p) != manifest[name]["sha256"]:
            raise ValueError(f"SHA-256 mismatch for {name} in {path}: refusing to load")
    return got


def _confirm(yes):
    if yes or os.environ.get("DECISION_TUNE_YES") == "1":
        return True
    if not sys.stdin.isatty():
        raise RuntimeError("DecisionTune 1.0 is not downloaded yet. Run `decision-tune download --yes`, "
                           "set DECISION_TUNE_YES=1, or pass yes=True.")
    try:
        return input(PROMPT).strip().lower() in ("", "y", "yes")
    except EOFError:
        return False


def download(repo=REPO, backend="auto", yes=None):
    """Local snapshot path of `repo` with the files `backend` needs, downloading (after asking) into the HF cache if missing."""
    from huggingface_hub import snapshot_download

    want = ["manifest.json", *FILES[pick_backend(backend)]]
    try:
        path = snapshot_download(repo, allow_patterns=want, local_files_only=True)
        if all(os.path.isfile(os.path.join(path, f)) for f in want):
            return path
    except Exception:  # not cached yet
        pass
    if not _confirm(yes):
        raise SystemExit("Download cancelled.")
    return snapshot_download(repo, allow_patterns=want)


def resolve(repo_or_dir=REPO, backend="auto", yes=None):
    """Verified local directory for a Hub repo id or a local model directory."""
    p = os.path.expanduser(repo_or_dir)
    path = p if os.path.isdir(p) else download(repo_or_dir, backend, yes)
    verify(path, FILES[pick_backend(backend)], MANIFEST_SHA256 if repo_or_dir == REPO else None)
    return path


class DecisionModel(_Engine):
    @classmethod
    def from_pretrained(cls, repo_or_dir=REPO, backend="auto", device=None, dtype="float32", yes=None):
        """Load from the Hub (default: DecisionTune 1.0) or a local directory. Every model file is SHA-256 checked first."""
        backend = pick_backend(backend)
        return cls(resolve(repo_or_dir, backend, yes), backend=backend, device=device, dtype=dtype)
