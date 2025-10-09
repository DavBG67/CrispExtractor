#!/usr/bin/env python3
"""
Script pour exporter les messages de chaque conversation listée dans
`conversations/conversations.jsonl` en utilisant l'API Crisp.

Fonctionnalités principales:
- Pour chaque conversation (session_id) crée/met à jour un fichier
  `conversations/messages/{session_id}.jsonl` contenant tous les messages
  (une ligne JSON par message) triés du plus récent au plus ancien.
- Si le fichier existe déjà, n'ajoute que les messages nouveaux (dédup par
  fingerprint / id / created_at) et s'arrête quand l'API ne retourne que
  des messages déjà présents.
- Gère la pagination via le paramètre `timestamp_before` (valeur minimale
  du timestamp renvoyé par la page précédente).
- Gère les codes HTTP 200 et 206 comme valides. Traite proprement 429.
- Sauvegarde un fichier d'état `conversations/messages/messages.jsonl.state.json`
  pour reprendre la liste des conversations (index dans le fichier conversations.jsonl).

Variables d'environnement requises:
- CRISP_IDENTIFIER_PROD (identifiant API)
- CRISP_KEY_PROD (clé API)
- ID_SITE_CRISP (website_id)

Options CLI:
- --nb N : nombre max de conversations à traiter (défaut 50)
- --reset : réinitialise le fichier d'état (recommence depuis le début)

Commentaires et messages de log en français.
"""

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import requests

# Chemins (peuvent être monkeypatchés dans les tests)
CONV_DIR = os.path.join(os.path.dirname(__file__), "conversations")
CONV_FILE = os.path.join(CONV_DIR, "conversations.jsonl")
MESS_DIR = os.path.join(CONV_DIR, "messages")
MESS_STATE_FILE = os.path.join(MESS_DIR, "messages.jsonl.state.json")

HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}

DEFAULT_NB = 50


def load_messages_state() -> Dict[str, Any]:
    if not os.path.exists(MESS_STATE_FILE):
        return {"next_index": 0}
    try:
        with open(MESS_STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"next_index": 0}


def save_messages_state(state: Dict[str, Any]) -> None:
    os.makedirs(MESS_DIR, exist_ok=True)
    with open(MESS_STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def read_conversations_list() -> List[Dict[str, Any]]:
    """Lit `conversations.jsonl` et retourne la liste d'objets JSON, ligne par ligne."""
    out: List[Dict[str, Any]] = []
    if not os.path.exists(CONV_FILE):
        return out
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


def session_id_from_conv(conv: Dict[str, Any]) -> Optional[str]:
    # Support des noms possibles
    return conv.get("session_id") or conv.get("session")


def call_crisp_messages_api(website_id: str, session_id: str, auth: Tuple[str, str], timestamp_before: Optional[int] = None) -> requests.Response:
    """Appel API vers l'endpoint messages d'une conversation.

    signature compatible avec les tests (peut être monkeypatchée).
    """
    url = f"https://api.crisp.chat/v1/website/{website_id}/conversation/{session_id}/messages/"
    params = {}
    if timestamp_before is not None:
        params["timestamp_before"] = str(timestamp_before)
    resp = requests.get(url, headers=HEADERS, params=params, auth=auth, timeout=30)
    return resp


def _message_key(msg: Dict[str, Any]) -> str:
    """Clé unique pour un message: fingerprint || id || created_at.
    Toujours retourne une chaîne pour faciliter la déduplication.
    """
    k = msg.get("fingerprint") or msg.get("id") or msg.get("uuid") or msg.get("created_at") or msg.get("timestamp")
    return str(k) if k is not None else json.dumps(msg, ensure_ascii=False)


def _message_timestamp(msg: Dict[str, Any]) -> int:
    # Certains messages utilisent 'timestamp', d'autres 'created_at'
    val = msg.get("timestamp")
    if val is None:
        val = msg.get("created_at")
    try:
        return int(val or 0)
    except Exception:
        return 0


def read_existing_messages(session_file: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not os.path.exists(session_file):
        return out
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


def write_messages_file(session_file: str, messages: List[Dict[str, Any]]) -> None:
    os.makedirs(os.path.dirname(session_file), exist_ok=True)
    with open(session_file, "w", encoding="utf-8") as fh:
        for m in messages:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")


def process_session(website_id: str, session_id: str, auth: Tuple[str, str]) -> Tuple[int, int]:
    """Traite une seule session: télécharge les messages nouveaux et met à jour le fichier.

    Retourne (added_count, total_after)
    """
    os.makedirs(MESS_DIR, exist_ok=True)
    session_file = os.path.join(MESS_DIR, f"{session_id}.jsonl")

    existing_msgs = read_existing_messages(session_file)
    existing_keys: Set[str] = set(_message_key(m) for m in existing_msgs)

    added = 0

    # pagination: on récupère les pages en partant des plus récentes (pas de timestamp_before),
    # puis en descendant en utilisant le timestamp le plus ancien retourné.
    timestamp_before: Optional[int] = None
    new_messages: List[Dict[str, Any]] = []

    while True:
        try:
            resp = call_crisp_messages_api(website_id, session_id, auth, timestamp_before=timestamp_before)
        except requests.RequestException as e:
            print(f"Erreur réseau lors de l'appel messages pour {session_id}: {e}")
            break

        if resp.status_code == 429:
            # quota atteint -> on signale et on renvoie sans modifications
            print(f"Quota API atteint (429) lors de la session {session_id}. Arrêt temporaire.")
            break

        if resp.status_code not in (200, 206):
            print(f"Réponse API inattendue pour messages {session_id}: {resp.status_code}")
            break

        try:
            payload = resp.json()
        except Exception:
            print(f"Réponse non-JSON pour messages {session_id}")
            break

        page_data = payload.get("data") or []
        if not page_data:
            break

        # Détection du timestamp minimal de la page (pour la pagination)
        page_ts_vals = [_message_timestamp(m) for m in page_data]
        page_min_ts = min(page_ts_vals) if page_ts_vals else None

        # Vérifier si cette page contient seulement des messages déjà connus
        new_in_page: List[Dict[str, Any]] = []
        for m in page_data:
            key = _message_key(m)
            if key not in existing_keys:
                new_in_page.append(m)
                existing_keys.add(key)

        if not new_in_page:
            # Si aucun nouveau message sur cette page -> on peut s'arrêter
            break

        # Ajouter les nouveaux (conserver l'ordre tel que renvoyé par l'API)
        new_messages.extend(new_in_page)
        added += len(new_in_page)

        # Préparer la prochaine page (prendre la valeur la plus ancienne - 1 pour être sûr)
        if page_min_ts is None:
            break
        timestamp_before = page_min_ts

        # petite pause pour politesse
        time.sleep(0.1)

    # Si on a récupéré des messages nouveaux, on doit fusionner et trier
    if new_messages:
        merged = existing_msgs + new_messages
        # Dédupliquer au cas où (garder le premier exemplaire le plus récent trouvé)
        seen: Set[str] = set()
        deduped: List[Dict[str, Any]] = []
        # Trier selon timestamp descendant
        merged_sorted = sorted(merged, key=_message_timestamp, reverse=True)
        for m in merged_sorted:
            k = _message_key(m)
            if k in seen:
                continue
            seen.add(k)
            deduped.append(m)

        write_messages_file(session_file, deduped)

    total_after = len(read_existing_messages(session_file))

    return added, total_after


def main(argv=None):
    parser = argparse.ArgumentParser(description="Exporter les messages des conversations listées dans conversations/conversations.jsonl")
    parser.add_argument("--nb", type=int, default=DEFAULT_NB, help="Nombre maximum de conversations à traiter (défaut 50)")
    parser.add_argument("--reset", action="store_true", help="Réinitialiser le fichier d'état et repartir du début")
    args = parser.parse_args(argv)

    max_to_process = args.nb

    crisp_id = os.getenv("CRISP_IDENTIFIER_PROD")
    crisp_key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")

    if not (crisp_id and crisp_key and website_id):
        print("ERREUR: CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définis")
        sys.exit(2)

    auth = (crisp_id, crisp_key)

    if args.reset and os.path.exists(MESS_STATE_FILE):
        try:
            os.remove(MESS_STATE_FILE)
            print("Fichier d'état supprimé (reset)")
        except Exception as e:
            print(f"Impossible de supprimer le fichier d'état: {e}")

    convs = read_conversations_list()
    session_ids: List[str] = []
    for c in convs:
        sid = session_id_from_conv(c)
        if sid:
            session_ids.append(sid)

    state = load_messages_state()
    next_index = int(state.get("next_index", 0) or 0)

    processed = 0
    idx = next_index

    while idx < len(session_ids) and processed < max_to_process:
        sid = session_ids[idx]
        print(f"Traitement de la conversation {sid} (index {idx})")
        added, total_after = process_session(website_id, sid, auth)
        print(f"Conversation {sid}: exportées={added}, total_dans_fichier={total_after}")

        processed += 1
        idx += 1
        # sauvegarder l'état pour pouvoir reprendre
        save_messages_state({"next_index": idx})

    print("--- Fin du traitement ---")
    print(f"Conversations traitées cette exécution: {processed}")


if __name__ == "__main__":
    main()
