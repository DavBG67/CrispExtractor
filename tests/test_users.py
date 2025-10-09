import os
import sys
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

import users


class DummyResp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_users_export_nb_and_reset(tmp_path, monkeypatch):
    # Préparer répertoires
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    users_dir = tmp_path / "utilisateurs"
    users_dir.mkdir()

    # monkeypatch des chemins
    monkeypatch.setattr(users, "CONV_DIR", str(conv_dir))
    monkeypatch.setattr(users, "CONV_FILE", str(conv_dir / "conversations.jsonl"))
    monkeypatch.setattr(users, "USERS_DIR", str(users_dir))
    monkeypatch.setattr(users, "USERS_FILE", str(users_dir / "utilisateurs.jsonl"))

    # créer un fichier conversations.jsonl avec 3 emails
    convs = [
        {"meta": {"email": "b@example.com"}},
        {"meta": {"email": "a@example.com"}},
        {"meta": {"email": "c@example.com"}},
    ]
    with open(users.CONV_FILE, "w", encoding="utf-8") as fh:
        for c in convs:
            fh.write(json.dumps(c) + "\n")

    # préparer réponses API: pour a@example.com and b@example.com
    responses = {
        "a@example.com": DummyResp(200, {"email": "a@example.com", "name": "A"}),
        "b@example.com": DummyResp(200, {"email": "b@example.com", "name": "B"}),
        "c@example.com": DummyResp(200, {"email": "c@example.com", "name": "C"}),
    }

    def fake_call(website_id, people_id, auth):
        # people_id received by code is lowercase email
        return responses.get(people_id)

    monkeypatch.setattr(users, "call_crisp_people_profile", fake_call)

    # monkeypatch env vars
    monkeypatch.setenv("CRISP_IDENTIFIER_PROD", "id")
    monkeypatch.setenv("CRISP_KEY_PROD", "key")
    monkeypatch.setenv("ID_SITE_CRISP", "wid")

    # run with --nb 2
    users.main(["--nb", "2", "--reset"])  # reset = True

    # vérifier que le fichier existe et contient 2 utilisateurs triés par email
    with open(users.USERS_FILE, "r", encoding="utf-8") as fh:
        lines = [json.loads(l) for l in fh if l.strip()]

    assert len(lines) == 2
    assert [l["email"] for l in lines] == sorted([l["email"] for l in lines])
