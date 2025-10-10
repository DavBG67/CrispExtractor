#!/usr/bin/env python3
"""
mess.py

Script pour exporter les messages de chaque conversation listée dans
`/conversations/conversations.jsonl` en utilisant l'API Crisp.

Fonctionnalités principales:
- Lit le fichier de conversations et pour chaque `session_id` crée/met à jour
  `/conversations/messages/{session_id}.jsonl` contenant tous les messages
  triés du plus récent au plus ancien.
- Gère la pagination via le paramètre `timestamp_before` fourni par l'API Crisp.
- Gère un fichier d'état pour reprendre le traitement:
  `/conversations/messages/messages.jsonl.state.json`.
- Deduplication des messages par champ `fingerprint` (identifiant unique).

Commentaires en français.
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import requests

# Racine du projet
ROOT = Path(__file__).resolve().parent

# Emplacements par défaut (peuvent être patchés par les tests via monkeypatch)
CONVS_FILE = ROOT / "conversations" / "conversations.jsonl"
MESS_DIR = ROOT / "conversations" / "messages"
STATE_FILE = MESS_DIR / "messages.jsonl.state.json"

# Entêtes requis par l'API Crisp
HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}


def load_state() -> Dict[str, Any]:
    """Charge l'état de reprise (next_index).
    Retourne un dict vide si aucun état trouvé ou fichier malformé.
    """
    try:
        if STATE_FILE.exists():
            with STATE_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return {}
    return {}


def save_state(state: Dict[str, Any]) -> None:
    """Sauvegarde l'état de reprise.
    Crée le dossier si nécessaire.
    """
    MESS_DIR.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def merge_and_sort_messages(existing: List[Dict[str, Any]], new: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Fusionne deux listes de messages en évitant les doublons par `fingerprint`.

    - Les messages sans champ `fingerprint` dans la nouvelle liste sont ignorés.
    - Le résultat est trié par `timestamp` descendant (du plus récent au plus ancien).
    - Les fingerprints sont comparés en tant que chaînes pour plus de robustesse.

    Retourne la liste fusionnée triée.
    """
    index: Dict[str, Dict[str, Any]] = {}

    # Ajouter les messages existants
    for m in existing:
        fp = m.get("fingerprint")
        if fp is None:
            continue
        index[str(fp)] = m

    # Ajouter/update avec les nouveaux (ignorer ceux sans fingerprint)
    for m in new:
        fp = m.get("fingerprint")
        if fp is None:
            # ignorer (pas d'identifiant unique)
            continue
        index[str(fp)] = m

    # Construire la liste triée par timestamp descendant
    def ts(item: Dict[str, Any]) -> int:
        t = item.get("timestamp")
        if isinstance(t, int):
            return t
        try:
            return int(t)
        except Exception:
            return 0

    merged = list(index.values())
    merged.sort(key=ts, reverse=True)
    return merged


def read_jsonl_file(path: Path) -> List[Dict[str, Any]]:
    """Lit un fichier JSONL et retourne la liste d'objets (ignore les lignes vides).
    En cas d'erreur retourne une liste vide.
    """
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    # ignorer lignes malformées
                    continue
    except Exception:
        return []
    return out


def write_jsonl_file(path: Path, items: List[Dict[str, Any]]) -> None:
    """Écrit une liste d'objets en JSONL (écrase le fichier).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")


def call_messages_api(website_id: str, session_id: str, auth: Tuple[str, str], timestamp_before: Optional[int] = None) -> Optional[requests.Response]:
    """Appelle l'API Crisp pour récupérer les messages d'une conversation.

    Utilise le paramètre `timestamp_before` pour la pagination.
    Retourne l'objet Response ou None en cas d'erreur réseau.
    """
    url = f"https://api.crisp.chat/v1/website/{website_id}/conversation/{session_id}/messages/"
    params = {}
    if timestamp_before is not None:
        params["timestamp_before"] = timestamp_before

    try:
        resp = requests.get(url, headers=HEADERS, auth=auth, params=params, timeout=30)
        return resp
    except requests.RequestException as e:
        print(f"Erreur réseau lors de l'appel messages API: {e}")
        return None


def extract_session_id_from_line(obj: Dict[str, Any]) -> Optional[str]:
    """Extraire session_id depuis une ligne de conversations (format similaire à conv.extract_session_id).
    On essaye quelques clés communes.
    """
    if not isinstance(obj, dict):
        return None
    for key in ("session_id", "id", "_id"):
        if key in obj and isinstance(obj[key], str):
            return obj[key]
    if "data" in obj and isinstance(obj["data"], dict):
        s = obj["data"].get("session_id")
        if isinstance(s, str):
            return s
    return None


def process_conversations(nb: int = 50, reset: bool = False) -> None:
    """Traitement principal: parcourt les conversations et exporte les messages.

    - nb : nombre maximum de conversations à traiter cette exécution.
    - reset : réinitialise le fichier d'état pour repartir depuis le début.
    """
    # Vérifier variables d'environnement
    identifier = os.getenv("CRISP_IDENTIFIER_PROD")
    key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")
    if not identifier or not key or not website_id:
        print("Les variables d'environnement CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définies.")
        sys.exit(1)

    auth = (identifier, key)

    # Préparer dossiers
    MESS_DIR.mkdir(parents=True, exist_ok=True)

    if reset:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        print("État réinitialisé (reset). Reprise depuis le début du fichier de conversations.")

    # Charger état
    state = load_state()
    next_index = int(state.get("next_index", 0))

    # Lire toutes les conversations (JSONL)
    if not CONVS_FILE.exists():
        print(f"Fichier de conversations introuvable: {CONVS_FILE}")
        return

    conv_lines = []
    with CONVS_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                conv_lines.append(json.loads(line))
            except Exception:
                conv_lines.append(None)

    total_convs = len(conv_lines)
    processed = 0
    ignored_convs = 0

    idx = next_index
    while idx < total_convs and processed < nb:
        obj = conv_lines[idx]
        idx += 1
        if not isinstance(obj, dict):
            ignored_convs += 1
            continue

        session_id = extract_session_id_from_line(obj)
        if not session_id:
            ignored_convs += 1
            continue

        processed += 1
        print(f"Traitement conversation {session_id} ({processed}/{nb}) ...")

        msg_file = MESS_DIR / f"{session_id}.jsonl"

        # Lire messages existants
        existing = read_jsonl_file(msg_file)
        existing_fps = {str(m.get("fingerprint")) for m in existing if m.get("fingerprint") is not None}

        # Pagination: on commence sans timestamp_before, puis on utilise le plus ancien timestamp
        more = True
        page = 0
        new_messages_acc: List[Dict[str, Any]] = []
        oldest_ts: Optional[int] = None

        while more:
            resp = call_messages_api(website_id, session_id, auth, timestamp_before=oldest_ts)
            page += 1
            if resp is None:
                print(f"Échec appel API pour {session_id}, arrêt de la conversation courante.")
                break

            if resp.status_code == 429:
                print("429 reçu: quota API atteint. Pause et arrêt du traitement.")
                # Sauvegarder l'état avant de quitter
                state["next_index"] = idx - 1  # reprendre sur cette conversation
                save_state(state)
                return

            if resp.status_code not in (200, 206):
                print(f"Réponse inattendue pour {session_id}: {resp.status_code} {getattr(resp, 'text', '')}")
                break

            try:
                data = resp.json()
            except Exception:
                print(f"Impossible de décoder JSON pour {session_id} page {page}.")
                break

            # La réponse peut être une liste de messages ou un dict contenant 'data'
            page_items: List[Dict[str, Any]] = []
            if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
                page_items = data["data"]
            elif isinstance(data, list):
                page_items = data
            else:
                # pas de messages
                page_items = []

            if not page_items:
                # pas de nouveaux messages sur cette page -> fin de pagination
                break

            # Ajouter messages non présents (par fingerprint)
            added = 0
            ignored = 0
            for m in page_items:
                fp = m.get("fingerprint")
                if fp is None:
                    ignored += 1
                    continue
                if str(fp) in existing_fps:
                    ignored += 1
                    continue
                new_messages_acc.append(m)
                existing_fps.add(str(fp))
                added += 1

            # Mettre à jour oldest_ts pour pagination suivante: on prend le timestamp le plus petit
            try:
                ts_vals = [int(m.get("timestamp", 0)) for m in page_items if m.get("timestamp") is not None]
                if ts_vals:
                    min_ts = min(ts_vals)
                    # Pour éviter de récupérer le même message, on demande timestamp_before = min_ts
                    # l'API retourne les messages strictement inférieurs à ce timestamp selon doc
                    oldest_ts = min_ts
            except Exception:
                pass

            # Petite pause pour limiter la rapidité des appels
            time.sleep(0.05)

            # Si l'API a retourné moins de 1 élément (ou aucun), on stoppe. Sinon on boucle.
            # Ici on laisse la boucle se terminer naturellement si la prochaine page est vide.

        # Si on a récupéré des messages, fusionner et écrire
        if new_messages_acc:
            merged = merge_and_sort_messages(existing, new_messages_acc)
            write_jsonl_file(msg_file, merged)
            print(f"Conversation {session_id}: {len(new_messages_acc)} messages ajoutés, {len(existing)} messages existants.")
        else:
            # Aucun nouveau message -> si fichier n'existait pas, créer un fichier vide
            if not msg_file.exists() and existing:
                write_jsonl_file(msg_file, existing)
            elif not msg_file.exists():
                # créer au moins un fichier vide
                write_jsonl_file(msg_file, [])
            print(f"Conversation {session_id}: aucun nouveau message.")

        # Sauvegarder l'état: prochaine conversation
        state["next_index"] = idx
        save_state(state)

    # Rapport final
    print("--- Récapitulatif ---")
    print(f"Conversations traitées cette exécution: {processed}")
    print(f"Conversations ignorées/malformed: {ignored_convs}")
    # compter le nombre de fichiers messages
    try:
        files_count = len(list(MESS_DIR.glob("*.jsonl")))
    except Exception:
        files_count = 0
    print(f"Fichiers .jsonl de conversations présents: {files_count}")


def main():
    parser = argparse.ArgumentParser(description="Exporter les messages Crisp par conversation en JSONL")
    parser.add_argument("--nb", type=int, default=50, help="Nombre max de conversations à traiter (défaut 50)")
    parser.add_argument("--reset", action="store_true", help="Réinitialiser le fichier d'état et repartir du début")
    args = parser.parse_args()

    process_conversations(nb=args.nb, reset=args.reset)


if __name__ == "__main__":
    main()
