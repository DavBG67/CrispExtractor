import sys
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mess import merge_and_sort_messages


def test_merge_and_sort_messages_basic():
    existing = [
        {"fingerprint": "f1", "timestamp": 100, "body": "old message 1"},
        {"fingerprint": "f2", "timestamp": 90, "body": "old message 2"},
    ]
    new = [
        {"fingerprint": "f3", "timestamp": 200, "body": "new message"},
        {"fingerprint": "f1", "timestamp": 100, "body": "old message 1"},
    ]

    merged = merge_and_sort_messages(existing, new)
    # ordre: f3 (200), f1 (100), f2 (90)
    assert [m["fingerprint"] for m in merged] == ["f3", "f1", "f2"]


def test_merge_ignores_no_fingerprint():
    existing = [{"fingerprint": "a", "timestamp": 50}]
    new = [{"timestamp": 200}, {"fingerprint": "b", "timestamp": 100}]
    merged = merge_and_sort_messages(existing, new)
    assert [m.get("fingerprint") for m in merged] == ["b", "a"]
