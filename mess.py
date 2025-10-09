#!/usr/bin/env python3
"""
Script pour exporter les messages de chaque conversation listée dans conversations/conversations.jsonl

Fonctionnalités principales:
- Lit le fichier conversations/conversations.jsonl et pour chaque session_id récupère les messages via l'API Crisp
- Stocke les messages dans conversations/messages/<session_id>.jsonl, triés du plus récent au plus ancien
- Gère la pagination via timestamp_before
- Utilise un fichier d'état conversations/messages/messages.jsonl.state.json pour reprendre là où on s'était arrêté
- Paramètres CLI: --nb (nombre max de conversations à traiter), --reset (réinitialise l'état)

Variables d'environnement attendues:
- CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD, ID_SITE_CRISP

Commentaires en français.
"""
import os
import sys
import json
import time
import argparse
from typing import Dict, Any, List, Optional, Tuple

import requests

ROOT_DIR = os.path.dirname(__file__)
CONV_DIR = os.path.join(ROOT_DIR, "conversations")
CONV_FILE = os.path.join(CONV_DIR, "conversations.jsonl")
MESS_DIR = os.path.join(CONV_DIR, "messages")
STATE_FILE = os.path.join(MESS_DIR, "messages.jsonl.state.json")

HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}

DEFAULT_NB = 50


def load_conversations_list() -> List[Dict[str, Any]]:
    """Lit le fichier conversations.jsonl et retourne la liste d'objets.
    Chaque ligne est un objet JSON représentant une conversation.
    """
    if not os.path.exists(CONV_FILE):
        return []
    out = []
    with open(CONV_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def extract_session_id(conv: Dict[str, Any]) -> Optional[str]:
    """Extrait le session_id d'un enregistrement de conversation.
    Compatible avec la fonction du script conv.py
    """
    return conv.get("session_id") or conv.get("session") or None


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {"next_index": 0}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"next_index": 0}


def save_state(state: Dict[str, Any]) -> None:
    os.makedirs(MESS_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def read_existing_messages(session_file: str) -> List[Dict[str, Any]]:
    """Lit le fichier messages existant pour une session et retourne la liste d'objets.
    Le fichier est au format jsonl (une ligne = un message JSON).
    Les messages sont supposés être ordonnés du plus récent au plus ancien.
    """
    if not os.path.exists(session_file):
        return []
    out = []
    with open(session_file, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def write_messages(session_file: str, messages: List[Dict[str, Any]]) -> None:
    """Écrit une liste de messages dans le fichier jsonl (remplace le fichier)."""
    os.makedirs(os.path.dirname(session_file), exist_ok=True)
    with open(session_file, "w", encoding="utf-8") as fh:
        for m in messages:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")


def call_crisp_messages_api(website_id: str, session_id: str, auth: Tuple[str, str], timestamp_before: Optional[int] = None) -> requests.Response:
    """Appelle l'API Crisp pour récupérer les messages d'une conversation.
    Utilise le paramètre timestamp_before pour la pagination.
    """
    url = f"https://api.crisp.chat/v1/website/{website_id}/conversation/{session_id}/messages/"
    params = {}
    if timestamp_before is not None:
        params["timestamp_before"] = str(int(timestamp_before))
    resp = requests.get(url, headers=HEADERS, auth=auth, params=params, timeout=30)
    return resp


def merge_messages(existing: List[Dict[str, Any]], new_msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fusionne deux listes de messages en évitant les doublons.
    On considère l'identifiant 'id' du message lorsqu'il existe, sinon on compare l'ensemble JSON.
    Retourne la liste triée du plus récent au plus ancien.
    """
    seen = set()
    out = []
    # existing et new_msgs sont supposés être déjà du plus récent au plus ancien
    for m in new_msgs:
        key = m.get("id") or json.dumps(m, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    for m in existing:
        key = m.get("id") or json.dumps(m, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    # out est actuellement new_msgs (récent->ancien) suivis d'existing uniques.
    return out


def process_session(website_id: str, session_id: str, auth: Tuple[str, str]) -> Tuple[int, int]:
    """Traite une session: récupère messages via l'API, met à jour le fichier local.
    Retourne (added_count, total_after).
    """
    session_file = os.path.join(MESS_DIR, f"{session_id}.jsonl")
    existing = read_existing_messages(session_file)
    existing_ids = set((m.get("id") or json.dumps(m, sort_keys=True)) for m in existing)

    added = 0

    # pour la pagination, on commence par None (récupère les messages les plus récents)
    timestamp_before = None
    all_new = []

    while True:
        try:
            resp = call_crisp_messages_api(website_id, session_id, auth, timestamp_before)
        except requests.RequestException as e:
            print(f"Erreur réseau pour session {session_id}: {e}")
            break

        if resp.status_code == 429:
            print(f"Quota API atteint (429) lors de la session {session_id}. Arrêt de la récupération pour cette session.")
            break

        if resp.status_code not in (200, 206):
            print(f"Réponse API inattendue pour session {session_id}: {resp.status_code}")
            break

        try:
            payload = resp.json()
        except Exception:
            print(f"Réponse non JSON pour session {session_id}.")
            break

        data = payload.get("data") or []
        if not data:
            # aucune donnée: on arrête
            break

        # data est en principe du plus récent au plus ancien selon la doc
        new_found = 0
        for m in data:
            key = m.get("id") or json.dumps(m, sort_keys=True)
            if key in existing_ids:
                # si on rencontre un message déjà connu, on suppose qu'on a déjà tous les suivants
                continue
            existing_ids.add(key)
            all_new.append(m)
            new_found += 1

        if new_found == 0:
            # plus de nouveaux messages dans cette page -> on peut arrêter
            break

        # préparer la pagination: récupérer le timestamp le plus ancien de la page
        try:
            oldest = data[-1]
            ts = oldest.get("created_at") or oldest.get("timestamp") or None
            if ts:
                timestamp_before = int(ts)
            else:
                # si aucun timestamp, on s'arrête
                break
        except Exception:
            break

        # respecter une courte pause
        time.sleep(0.1)

    # fusionner et écrire si nécessaire
    if all_new:
        merged = merge_messages(existing, all_new)
        write_messages(session_file, merged)
        added = len(merged) - len(existing)
        total_after = len(merged)
    else:
        total_after = len(existing)

    return added, total_after


def main(argv=None):
    parser = argparse.ArgumentParser(description="Exporter les messages pour chaque conversation listée dans conversations/conversations.jsonl")
    parser.add_argument("--nb", type=int, default=DEFAULT_NB, help=f"Nombre maximum de conversations à traiter (défaut {DEFAULT_NB})")
    parser.add_argument("--reset", action="store_true", help="Réinitialiser le fichier d'état messages/messages.jsonl.state.json")
    args = parser.parse_args(argv)

    crisp_id = os.getenv("CRISP_IDENTIFIER_PROD")
    crisp_key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")

    if not (crisp_id and crisp_key and website_id):
        print("ERREUR: variables d'environnement CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définies.")
        sys.exit(2)

    auth = (crisp_id, crisp_key)

    if args.reset:
        try:
            if os.path.exists(STATE_FILE):
                os.remove(STATE_FILE)
            print("Réinitialisation: état des messages supprimé.")
        except Exception as e:
            print(f"Impossible de réinitialiser l'état: {e}")

    convs = load_conversations_list()
    state = load_state()
    next_index = int(state.get("next_index", 0) or 0)

    total_to_process = args.nb
    processed = 0

    if not convs:
        print("Aucune conversation trouvée dans conversations/conversations.jsonl")
        return

    # Parcours à partir de next_index
    for idx in range(next_index, len(convs)):
        if processed >= total_to_process:
            break
        conv = convs[idx]
        sid = extract_session_id(conv)
        if not sid:
            print(f"Conversation à l'index {idx} sans session_id, ignorée.")
            next_index = idx + 1
            save_state({"next_index": next_index})
            continue

        print(f"Traitement conversation {sid} (index {idx})")
        added, total_after = process_session(website_id, sid, auth)
        print(f"Session {sid}: messages ajoutés={added}, total_dans_fichier={total_after}")

        processed += 1
        next_index = idx + 1
        save_state({"next_index": next_index})

    print("--- Résumé ---")
    print(f"Conversations traitées cette exécution: {processed}")
    print(f"Prochaine index pour reprise: {next_index}")


if __name__ == "__main__":
    main()
