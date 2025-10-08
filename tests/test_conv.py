import json
from pathlib import Path

import pytest

from conv import append_conversations, sort_conversations_file, load_existing_ids


def make_conv(cid: str, last: int):
    return {"id": cid, "active": {"last": last}, "content": {"example": True}}


def test_append_and_load(tmp_path):
    f = tmp_path / "conversations.jsonl"
    convs = [make_conv("a", 100), make_conv("b", 200)]
    append_conversations(f, convs)
    ids = load_existing_ids(f)
    assert ids == {"a", "b"}


def test_sorting(tmp_path):
    f = tmp_path / "conversations.jsonl"
    convs = [make_conv("a", 100), make_conv("b", 300), make_conv("c", 200)]
    append_conversations(f, convs)
    # Importer la fonction locale de tri
    sort_conversations_file(f)
    lines = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    # Après tri: b (300), c (200), a (100)
    assert [l["id"] for l in lines] == ["b", "c", "a"]
