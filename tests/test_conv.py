import json
from pathlib import Path

import pytest

from conv import (
    append_conversations,
    extract_conversation_id,
    load_existing_ids,
    sort_conversations_file,
)


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


def test_extract_conversation_id_variants():
    payloads = [
        {"id": "abc"},
        {"_id": "def"},
        {"conversation_id": "ghi"},
        {"session_id": "jkl"},
        {"session": {"id": "mno"}},
        {"conversation": {"conversation_id": "pqr"}},
        {"meta": {"_id": "stu"}},
        {"data": {"session": {"session_id": "vwx"}}},
    ]
    expected = {"abc", "def", "ghi", "jkl", "mno", "pqr", "stu", "vwx"}
    assert {extract_conversation_id(p) for p in payloads} == expected


def test_load_existing_ids_with_nested_structures(tmp_path):
    f = tmp_path / "conversations.jsonl"
    conversations = [
        {"session": {"id": "sess-1"}},
        {"meta": {"conversation_id": "meta-2"}},
        {"data": {"session": {"session_id": "sess-3"}}},
    ]
    append_conversations(f, conversations)
    ids = load_existing_ids(f)
    assert ids == {"sess-1", "meta-2", "sess-3"}
