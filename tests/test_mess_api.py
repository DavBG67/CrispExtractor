import json
import os
from pathlib import Path

import pytest

import mess


class DummyResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


def test_process_conversations_creates_files(tmp_path, monkeypatch):
    # Préparer un petit fichier conversations.jsonl
    convs_dir = tmp_path / "conversations"
    convs_dir.mkdir()
    convs_file = convs_dir / "conversations.jsonl"

    session_id = "session_test_1"
    convs_file.write_text(json.dumps({"session_id": session_id, "website_id": "w1"}) + "\n")

    # remplacer les chemins dans mess.py pour pointer vers tmp
    monkeypatch.setattr(mess, "ROOT", tmp_path)
    monkeypatch.setattr(mess, "CONVS_FILE", convs_file)
    messages_dir = convs_dir / "messages"
    monkeypatch.setattr(mess, "MESS_DIR", messages_dir)
    monkeypatch.setattr(mess, "STATE_FILE", messages_dir / "messages.jsonl.state.json")

    # mock de requests.get qui retourne deux pages, puis vide
    calls = {"count": 0}

    def fake_get(url, headers, auth, params, timeout):
        # première page: deux messages
        if calls["count"] == 0:
            calls["count"] += 1
            return DummyResponse(200, [
                {"fingerprint": 1, "timestamp": 200, "body": "m1"},
                {"fingerprint": 2, "timestamp": 100, "body": "m2"},
            ])
        # deuxième page: un message plus ancien
        if calls["count"] == 1:
            calls["count"] += 1
            return DummyResponse(200, [
                {"fingerprint": 3, "timestamp": 50, "body": "m3"},
            ])
        return DummyResponse(200, [])

    monkeypatch.setattr(mess.requests, "get", fake_get)

    # définir variables d'environnement nécessaires
    monkeypatch.setenv("CRISP_IDENTIFIER_PROD", "id")
    monkeypatch.setenv("CRISP_KEY_PROD", "key")
    monkeypatch.setenv("ID_SITE_CRISP", "w1")

    # exécuter
    mess.process_conversations(nb=1, reset=True)

    # vérifier la présence du fichier messages
    msg_file = messages_dir / f"{session_id}.jsonl"
    assert msg_file.exists()

    lines = [json.loads(l) for l in msg_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    # ordre attendu: fingerprint 1 (200), 2 (100), 3 (50)
    assert [m["fingerprint"] for m in lines] == [1, 2, 3]
