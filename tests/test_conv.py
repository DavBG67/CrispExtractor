import os
import json
import tempfile
import sys
from pathlib import Path
import pytest

# Ajouter le répertoire racine au sys.path pour importer conv.py
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Tests de base pour conv.py (import minimal)

from conv import extract_session_id, sort_conversations


def test_extract_session_id_variants():
    assert extract_session_id({'session_id': 'abc'}) == 'abc'
    assert extract_session_id({'id': '123'}) == '123'
    assert extract_session_id({'data': {'session_id': 'zzz'}}) == 'zzz'
    assert extract_session_id({'no': 'id'}) is None


def test_sort_conversations():
    a = {'session_id': '1', 'active': {'last': 100}}
    b = {'session_id': '2', 'active': {'last': 200}}
    c = {'session_id': '3', 'active': {'last': 50}}
    res = sort_conversations([a, b, c])
    assert res[0]['session_id'] == '2'
    assert res[1]['session_id'] == '1'
    assert res[2]['session_id'] == '3'
