import json
import os
from pathlib import Path

import pytest

from conv import STATE_FILE, CONV_FILE, write_state, read_state


def test_reset_option(tmp_path, monkeypatch, capsys):
    # Préparer un faux dossier conversations
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()
    f = conv_dir / "conversations.jsonl"
    state = conv_dir / "conversations.jsonl.state.json"

    # Créer des fichiers factices
    f.write_text(json.dumps({"id": "x"}) + "\n", encoding="utf-8")
    state.write_text(json.dumps({"page": 5}), encoding="utf-8")

    # Forcer les chemins dans le module pour pointer vers tmp_path
    monkeypatch.setattr("conv.CONV_DIR", conv_dir)
    monkeypatch.setattr("conv.CONV_FILE", f)
    monkeypatch.setattr("conv.STATE_FILE", state)

    # Appeler le script avec --reset
    from conv import main

    rc = main(["--reset"])
    captured = capsys.readouterr()

    # Vérifier que les fichiers ont été supprimés
    assert not f.exists()
    assert not state.exists()
    assert rc == 0
    assert "supprimé" in captured.out or "Supprimé" in captured.out


def test_state_read_write(tmp_path):
    state_file = tmp_path / "conversations" / "conversations.jsonl.state.json"
    state_file.parent.mkdir(parents=True)
    write_state(state_file, {"page": 2})
    s = read_state(state_file)
    assert s.get("page") == 2
