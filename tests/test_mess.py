import os
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

import mess


def make_msg(mid, ts):
    return {"id": mid, "created_at": ts, "content": {"type": "text", "content": "hello"}}


def test_process_session_append_and_no_new(tmp_path, monkeypatch):
    # Préparer dossier conversations et fichiers
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    msgs_dir = conv_dir / "messages"
    msgs_dir.mkdir()

    # monkeypatch des chemins
    monkeypatch.setattr(mess, "CONV_DIR", str(conv_dir))
    monkeypatch.setattr(mess, "CONV_FILE", str(conv_dir / "conversations.jsonl"))
    monkeypatch.setattr(mess, "MESS_DIR", str(msgs_dir))

    # créer un fichier existant pour une session
    session_id = "s1"
    session_file = msgs_dir / f"{session_id}.jsonl"
    existing = [make_msg("m1", 200), make_msg("m2", 100)]
    with open(session_file, "w", encoding="utf-8") as fh:
        for m in existing:
            fh.write(json.dumps(m) + "\n")

    # Mock de call_crisp_messages_api: retourne une page avec un nouveau message, puis vide
    responses = []
    class DummyResp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
        def json(self):
            return self._payload

    # Première page: un message plus récent
    responses.append(DummyResp(200, {"data": [make_msg("m3", 300)]}))
    # Deuxième page: pas de données
    responses.append(DummyResp(200, {"data": []}))

    def fake_api(website_id, session_id_arg, auth, timestamp_before=None):
        return responses.pop(0)

    monkeypatch.setattr(mess, "call_crisp_messages_api", fake_api)

    added, total_after = mess.process_session("wid", session_id, ("id", "key"))
    assert added == 1
    assert total_after == 3

    # Re-run: now API returns only existing messages -> no ajout
    responses2 = [DummyResp(200, {"data": [make_msg("m3", 300), make_msg("m2", 100)]}), DummyResp(200, {"data": []})]
    responses[:] = responses2
    added2, total_after2 = mess.process_session("wid", session_id, ("id", "key"))
    assert added2 == 0
    assert total_after2 == 3
