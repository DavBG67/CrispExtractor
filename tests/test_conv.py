import os
import json
import tempfile

import pytest

from types import SimpleNamespace

import importlib.util
import sys
from pathlib import Path

# Importer le module conv.py par chemin (compatible avec pytest depuis la racine)
conv_path = Path(__file__).resolve().parents[1] / "conv.py"
spec = importlib.util.spec_from_file_location("conv", str(conv_path))
conv = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = conv
spec.loader.exec_module(conv)


class DummyResponse:
    def __init__(self, status_code, json_data):
        self.status_code = status_code
        self._json = json_data

    def json(self):
        return self._json


def test_fetch_conversations_success(monkeypatch, tmp_path):
    # Simule une réponse API
    dummy = DummyResponse(200, {"data": [{"session_id": "s1", "active": {"last": 123}}], "cursor": None})

    def fake_get(url, headers, params, auth, timeout):
        return dummy

    monkeypatch.setattr(conv.requests, "get", fake_get)

    ok, data, cursor = conv.fetch_conversations("siteid", limit=1, cursor=None, auth=("id","key"))
    assert ok is True
    assert isinstance(data, list)
    assert data[0]["session_id"] == "s1"
    assert cursor is None
