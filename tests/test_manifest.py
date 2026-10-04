import hashlib
import json
import os

import pytest

from decision_tune.hub import MANIFEST_SHA256, sha256, verify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_pinned_hash_matches_manifest_in_repo():
    assert sha256(os.path.join(ROOT, "manifest.json")) == MANIFEST_SHA256


def test_engine_in_package_matches_manifest():
    files = json.load(open(os.path.join(ROOT, "manifest.json")))["files"]
    assert sha256(os.path.join(ROOT, "src", "decision_tune", "engine.py")) == files["engine.py"]["sha256"]


def test_verify_refuses_tampered_file(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"weights")
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {"a.bin": {"sha256": hashlib.sha256(b"weights").hexdigest()}}}))
    verify(str(tmp_path), ["a.bin"])
    (tmp_path / "a.bin").write_bytes(b"weightz")
    with pytest.raises(ValueError, match="mismatch"):
        verify(str(tmp_path), ["a.bin"])
    with pytest.raises(ValueError, match="expected"):
        verify(str(tmp_path), [], pinned="0" * 64)
