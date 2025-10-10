#!/usr/bin/env python3
"""
mess.py

Script pour exporter les messages des conversations listées dans
`conversations/conversations.jsonl` en fichiers `/conversations/messages/{session_id}.jsonl`.

Principales fonctionnalités:
- Pagination via `timestamp_before`
- Evite les doublons en se basant sur `fingerprint`
- Fichier d'état pour reprendre (`conversations/messages/messages.jsonl.state.json`)
- Options CLI: --nb N (par défaut 50), --reset

Commentaires en français.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Any

import requests


ROOT = Path(__file__).resolve().parent
CONVS_FILE = ROOT / "conversations" / "conversations.jsonl"
MESS_DIR = ROOT / "conversations" / "messages"
STATE_FILE = MESS_DIR / "messages.jsonl.state.json"


def merge_and_sort_messages(existing: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fusionne deux listes de messages en évitant les doublons par `fingerprint`.

    Retourne la liste triée du plus récent au plus ancien (timestamp décroissant).
    Les messages sans champ `fingerprint` sont ignorés.
    """
    seen: Dict[str, Dict[str, Any]] = {}

    # Ajouter existants
    for m in existing:
        fp = m.get("fingerprint")
        if fp is None:
            continue
        seen[str(fp)] = m

    # Ajouter nouveaux (écrase si même fingerprint mais new remplace existing)
    for m in new:
        fp = m.get("fingerprint")
        if fp is None:
            continue
        seen[str(fp)] = m

    merged = list(seen.values())

    def _ts(m: Dict[str, Any]) -> int:
        t = m.get("timestamp", 0)
        try:
            return int(t)
        except Exception:
            return 0

    merged.sort(key=_ts, reverse=True)
    return merged


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out = []
    if not path.exists():
        return out
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                # ignorer lignes malformées
                continue
    return out


def _write_jsonl(path: Path, messages: List[Dict[str, Any]]) -> None:
    # écrire une ligne par message
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for m in messages:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")


def _load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"next_index": 0}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"next_index": 0}


def _save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def fetch_messages_from_api(session_id: str, website_id: str, auth: tuple, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    """Récupère tous les messages d'une conversation via l'API Crisp en paginant.

    Renvoie la liste brute des messages (ordre non garanti par l'API).
    Gère 200 et 206 comme valides et 429 avec backoff.
    """
    collected: List[Dict[str, Any]] = []
    timestamp_before: Optional[int] = None
    consecutive_429 = 0

    while True:
        url = f"https://api.crisp.chat/v1/website/{website_id}/conversation/{session_id}/messages/"
        params = {}
        if timestamp_before is not None:
            params["timestamp_before"] = str(timestamp_before)

        try:
            resp = requests.get(url, headers=headers, auth=auth, params=params, timeout=30)
        except Exception as e:
            print(f"Erreur requête API pour session {session_id}: {e}")
            break

        if resp.status_code == 429:
            consecutive_429 += 1
            wait = min(60, 2 ** min(consecutive_429, 6))
            print(f"429 Too Many Requests, attente {wait}s avant réessai...")
            time.sleep(wait)
            continue

        if resp.status_code not in (200, 206):
            print(f"Réponse inattendue {resp.status_code} pour session {session_id}: {resp.text}")
            break

        consecutive_429 = 0

        try:
            page = resp.json()
        except Exception:
            print(f"Réponse invalide JSON pour session {session_id}")
            break

        if not isinstance(page, list) or len(page) == 0:
            # plus rien
            break

        collected.extend(page)

        # déterminer timestamp le plus ancien de la page
        timestamps = [int(m.get("timestamp", 0)) for m in page if m.get("timestamp") is not None]
        if not timestamps:
            break
        min_ts = min(timestamps)
        # la page suivante doit récupérer les messages avant ce timestamp
        timestamp_before = min_ts - 1

        # petite pause pour limiter le risque de throttle
        time.sleep(0.1)

    return collected


def process_conversations(nb: int, reset: bool) -> None:
    # lire conversations
    if not CONVS_FILE.exists():
        print(f"Fichier de conversations introuvable: {CONVS_FILE}")
        return

    convs = []
    with CONVS_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                convs.append(json.loads(line))
            except Exception:
                continue

    total = len(convs)
    print(f"Conversations disponibles: {total}")

    # état
    if reset and STATE_FILE.exists():
        try:
            STATE_FILE.unlink()
        except Exception:
            pass

    state = _load_state()
    idx = int(state.get("next_index", 0))

    # auth & headers
    identifier = os.getenv("CRISP_IDENTIFIER_PROD")
    key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")

    if not identifier or not key or not website_id:
        print("Variables d'environnement CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP requises")
        return

    auth = (identifier, key)
    headers = {"Content-Type": "application/json", "X-Crisp-Tier": "plugin"}

    MESS_DIR.mkdir(parents=True, exist_ok=True)

    processed = 0
    skipped = 0
    files_created_or_updated = 0

    # parcourir conversations à partir de l'index
    for i in range(idx, len(convs)):
        if processed >= nb:
            break

        conv = convs[i]
        session_id = conv.get("session_id")
        website = conv.get("website_id", website_id)
        if not session_id:
            skipped += 1
            continue

        print(f"Traitement conversation {i} session_id={session_id}")

        msg_file = MESS_DIR / f"{session_id}.jsonl"
        existing_msgs = _read_jsonl(msg_file) if msg_file.exists() else []
        existing_fps: Set[str] = set()
        for m in existing_msgs:
            fp = m.get("fingerprint")
            if fp is not None:
                existing_fps.add(str(fp))

        # appeler API
        all_api_msgs = fetch_messages_from_api(session_id, website, auth, headers)

        # analyser pages et décider s'il y a des nouveaux messages
        new_msgs = []
        ignored = 0
        for m in all_api_msgs:
            fp = m.get("fingerprint")
            if fp is None:
                # ignorer messages sans fingerprint
                ignored += 1
                continue
            if str(fp) in existing_fps:
                ignored += 1
                continue
            new_msgs.append(m)

        if len(new_msgs) == 0:
            print(f" session {session_id}: aucun nouveau message (ignorés={ignored})")
            skipped += 1
            processed += 1
            # mettre à jour l'état pour la reprise
            state["next_index"] = i + 1
            _save_state(state)
            continue

        # fusionner et trier
        merged = merge_and_sort_messages(existing_msgs, new_msgs)
        _write_jsonl(msg_file, merged)
        files_created_or_updated += 1

        print(f" session {session_id}: exportés={len(new_msgs)} ignorés={ignored} total_after={len(merged)}")

        processed += 1
        # mettre à jour l'état
        state["next_index"] = i + 1
        _save_state(state)

        # courte pause
        time.sleep(0.05)

    # résumé
    existing_files = list(MESS_DIR.glob("*.jsonl"))
    print("--- Résumé ---")
    print(f"Conversations traitées: {processed}")
    print(f"Conversations ignorées (sans nouveau message ou problématiques): {skipped}")
    print(f"Fichiers .jsonl dans {MESS_DIR}: {len(existing_files)} (créés/mis à jour durant l'exécution: {files_created_or_updated})")


def _parse_args():
    p = argparse.ArgumentParser(description="Exporter messages Crisp par conversation")
    p.add_argument("--nb", type=int, default=50, help="nombre max de conversations à traiter (défaut 50)")
    p.add_argument("--reset", action="store_true", help="réinitialiser le fichier d'état")
    return p.parse_args()


def main():
    args = _parse_args()
    process_conversations(nb=args.nb, reset=args.reset)


if __name__ == "__main__":
    main()
