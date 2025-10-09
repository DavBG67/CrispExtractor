import os
import json
import tempfile
import sys
from pathlib import Path

# Ajouter la racine du projet dans sys.path pour que l'import de conv fonctionne
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from conv import extract_session_id, sort_conversations_by_last_active, write_conversations_to_file, read_all_conversations


def make_conv(session_id, last_active):
    return {"session_id": session_id, "active": {"last": last_active}, "other": 1}


def test_sort_and_dedup(tmp_path, monkeypatch):
    # préparer un répertoire conversations dans tmp_path
    conv_dir = tmp_path / "conversations"
    conv_dir.mkdir()

    # monkeypatch des chemins dans conv.py
    import conv
    monkeypatch.setattr(conv, "CONV_DIR", str(conv_dir))
    monkeypatch.setattr(conv, "CONV_FILE", str(conv_dir / "conversations.jsonl"))

    # créer quelques conversations et écrire
    convs = [make_conv("a", 100), make_conv("b", 200), make_conv("c", 150)]
    write_conversations_to_file(convs[:2])
    # lire et vérifier
    all1 = read_all_conversations()
    assert len(all1) == 2

    # ajouter une nouvelle convo et une existante
    write_conversations_to_file([convs[1], convs[2]])
    all2 = read_all_conversations()
    # should be 4 lines because write appends; dedup not performed here
    assert len(all2) == 4

    # test de tri
    sorted_convs = sort_conversations_by_last_active([convs[0], convs[1], convs[2]])
    assert [c["session_id"] for c in sorted_convs] == ["b", "c", "a"]
