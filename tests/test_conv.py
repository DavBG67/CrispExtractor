import json
import os
import sys
from pathlib import Path

import pytest

# S'assurer que le répertoire racine du projet est dans sys.path pour que
# l'import de conv fonctionne lors de la phase de collecte de pytest.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conv import CONV_DIR, CONV_FILE, STATE_FILE, run


class DummyResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def test_run_creates_file_and_writes(monkeypatch, tmp_path, capsys):
    # Préparer un répertoire temporaire
    cwd = Path.cwd()
    os.chdir(tmp_path)

    # Variables d'environnement factices
    os.environ["CRISP_IDENTIFIER_PROD"] = "id"
    os.environ["CRISP_KEY_PROD"] = "key"
    os.environ["ID_SITE_CRISP"] = "site"

    # Simuler requests.get pour retourner 3 conversations (dont une sans session_id)
    data = {
        "data": [
            {"session_id": "s1", "active": {"last": 200}},
            {"session": {"session_id": "s2"}, "active": {"last": 100}},
            {"active": {"last": 50}},
        ]
    }

    def fake_get(url, headers, params, auth, timeout):
        return DummyResponse(200, data)

    monkeypatch.setattr("conv.requests.get", fake_get)

    # Lancer avec nb=2 pour n'exporter que 2 nouvelles convo max
    rc = run(["--nb", "2"])
    assert rc == 0

    # Vérifier que le fichier a été créé
    assert CONV_FILE.exists()
    lines = CONV_FILE.read_text(encoding="utf-8").strip().splitlines()
    # Deux conversations exportées (s1 et s2)
    assert len(lines) == 2

    # Vérifier l'ordre après tri (s1 a active.last=200 puis s2=100)
    objs = [json.loads(l) for l in lines]
    assert objs[0].get("session_id") == "s1" or objs[0].get("session", {}).get("session_id") == "s1"

    # Nettoyer
    os.chdir(cwd)
