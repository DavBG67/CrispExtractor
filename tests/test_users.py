import sys
from pathlib import Path
import json
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import users


def make_conv_line(email: str) -> str:
    obj = {"data": {"meta": {"email": email}}}
    return json.dumps(obj)


class DummyResp:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.text = json.dumps(data)

    def json(self):
        return self._data


def test_extract_email_from_conversation_and_person(tmp_path, monkeypatch):
    # Préparer un fichier de conversations temporaire
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    conv_file = conv_dir / "conversations.jsonl"
    emails = ["a@example.com", "b@example.com", "a@example.com"]
    with conv_file.open("w", encoding="utf-8") as f:
        for e in emails:
            f.write(make_conv_line(e) + "\n")

    # patcher les chemins dans le module users
    monkeypatch.setattr(users, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(users, "CONV_DIR", conv_dir)
    monkeypatch.setattr(users, "CONV_FILE", conv_file)
    monkeypatch.setattr(users, "USERS_DIR", tmp_path / "utilisateurs")
    monkeypatch.setattr(users, "USERS_FILE", tmp_path / "utilisateurs" / "utilisateurs.jsonl")

    # Mock de requests.get pour retourner un profil pour a@example.com et b@example.com
    def fake_get(url, headers=None, auth=None, timeout=None):
        if "a%40example.com" in url:
            return DummyResp(200, {"email": "a@example.com", "name": "A"})
        if "b%40example.com" in url:
            return DummyResp(200, {"email": "b@example.com", "name": "B"})
        return DummyResp(404, {})

    monkeypatch.setenv("CRISP_IDENTIFIER_PROD", "id")
    monkeypatch.setenv("CRISP_KEY_PROD", "key")
    monkeypatch.setenv("ID_SITE_CRISP", "site123")

    monkeypatch.setattr(users.requests, "get", fake_get)

    # Exécuter main avec nb=2
    args = ["prog", "--nb", "2", "--reset"]
    monkeypatch.setattr(sys, "argv", args)
    users.main()

    # Vérifier que le fichier utilisateurs.jsonl existe et contient 2 entrées
    ufile = tmp_path / "utilisateurs" / "utilisateurs.jsonl"
    assert ufile.exists()
    lines = [json.loads(l) for l in ufile.read_text(encoding="utf-8").splitlines() if l.strip()]
    emails_out = sorted([users.extract_email_from_person(o) for o in lines])
    assert emails_out == ["a@example.com", "b@example.com"]
