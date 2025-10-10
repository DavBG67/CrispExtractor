#!/usr/bin/env python3
"""
mess.py

Script pour exporter les messages des conversations listées dans
`/conversations/conversations.jsonl` en utilisant l'API Crisp.

Pour chaque conversation (session_id) on crée/maj un fichier
`/conversations/messages/{session_id}.jsonl` contenant tous les messages
de la conversation triés du plus récent au plus ancien.

Comportement principal :
- Lecture du fichier `conversations/conversations.jsonl` pour obtenir la
  liste des `session_id`.
- Pour chaque conversation, on vérifie si le fichier existe et on le met
  à jour uniquement si l'API retourne des messages nouveaux (dédup par
  champ `fingerprint`).
- Pagination via `timestamp_before` : on recommence tant qu'on reçoit
  des messages nouveaux et que l'API renvoie des pages.
- Fichier d'état : `/conversations/messages/messages.jsonl.state.json`
  contient `next_index` pour reprendre la liste au prochain appel.

Options :
- --nb N : nombre maximum de conversations à traiter (défaut 50)
- --reset : réinitialise le fichier d'état

Variables d'environnement attendues :
- CRISP_IDENTIFIER_PROD (identifiant HTTP Basic)
- CRISP_KEY_PROD (clé HTTP Basic)
- ID_SITE_CRISP (website_id)

Headers requis : Content-Type: application/json, X-Crisp-Tier: plugin

"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests

# Constantes et chemins
ROOT_DIR = Path(__file__).parent
CONV_DIR = ROOT_DIR / "conversations"
CONV_FILE = CONV_DIR / "conversations.jsonl"
MESS_DIR = CONV_DIR / "messages"
STATE_FILE = MESS_DIR / "messages.jsonl.state.json"

HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}


def load_state() -> Dict[str, object]:
    """Charge l'état de traitement. Si absent renvoie un dict vide."""
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: Dict[str, object]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def read_conversations_list() -> List[Dict[str, object]]:
    """Lit `/conversations/conversations.jsonl` et retourne la liste d'objets.

    Chaque ligne doit être un JSON contenant au minimum la clé `session_id`.
    """
    res: List[Dict[str, object]] = []
    if not CONV_FILE.exists():
        return res
    with CONV_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                res.append(obj)
            except Exception:
                # ignorer les lignes malformées
                continue
    return res


def extract_session_id(conv_obj: Dict[str, object]) -> Optional[str]:
    """Extrait une session_id d'un objet conversation (plusieurs clés prises en charge)."""
    if not isinstance(conv_obj, dict):
        return None
    for key in ("session_id", "id", "_id"):
        v = conv_obj.get(key)
        if isinstance(v, str) and v:
            return v
    if "data" in conv_obj and isinstance(conv_obj["data"], dict):
        v = conv_obj["data"].get("session_id")
        if isinstance(v, str) and v:
            return v
    return None


def load_existing_messages(session_id: str) -> Tuple[List[Dict[str, object]], Set[str]]:
    """Charge les messages existants pour une conversation et renvoie (liste, set_fingerprints).

    Le fichier est `/conversations/messages/{session_id}.jsonl`.
    """
    path = MESS_DIR / f"{session_id}.jsonl"
    msgs: List[Dict[str, object]] = []
    fingerprints: Set[str] = set()
    if not path.exists():
        return msgs, fingerprints
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                m = json.loads(line)
                fp = m.get("fingerprint")
                if isinstance(fp, str):
                    fingerprints.add(fp)
                msgs.append(m)
            except Exception:
                continue
    return msgs, fingerprints


def merge_and_sort_messages(existing: List[Dict[str, object]], new: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Fusionne deux listes de messages en s'assurant d'aucun doublon par `fingerprint`.

    Le résultat est trié du plus récent au plus ancien sur la clé `timestamp` (desc).
    Les messages sans `fingerprint` sont ignorés.
    """
    seen: Set[str] = set()
    merged: List[Dict[str, object]] = []
    # ajouter tous les nouveaux (prioritaires car potentiellement plus récents)
    for m in new:
        fp = m.get("fingerprint")
        if not isinstance(fp, str):
            continue
        if fp in seen:
            continue
        seen.add(fp)
        merged.append(m)
    # ajouter existants si pas déjà présents
    for m in existing:
        fp = m.get("fingerprint")
        if not isinstance(fp, str):
            continue
        if fp in seen:
            continue
        seen.add(fp)
        merged.append(m)

    def ts_val(x: Dict[str, object]) -> int:
        t = x.get("timestamp")
        try:
            return int(t)
        except Exception:
            return 0

    merged.sort(key=ts_val, reverse=True)
    return merged


def call_messages_api(website_id: str, session_id: str, auth: Tuple[str, str], timestamp_before: Optional[int] = None) -> Optional[requests.Response]:
    """Appelle l'API Crisp pour récupérer les messages d'une conversation.

    Gère timestamp_before pour la pagination.
    """
    base = f"https://api.crisp.chat/v1/website/{website_id}/conversation/{session_id}/messages/"
    params = {}
    # par défaut on demande 50 éléments par page si le service le supporte
    params["per_page"] = 50
    if timestamp_before is not None:
        params["timestamp_before"] = int(timestamp_before)
    try:
        resp = requests.get(base, headers=HEADERS, auth=auth, params=params, timeout=30)
        return resp
    except requests.RequestException as e:
        print(f"Erreur réseau pour la conversation {session_id}: {e}")
        return None


def process_conversation(website_id: str, auth: Tuple[str, str], session_id: str) -> Tuple[int, int]:
    """Traite une conversation : renvoie (added, ignored).

    - added : nombre de messages ajoutés
    - ignored : nombre de messages ignorés (doublons ou sans fingerprint)
    """
    existing_msgs, existing_fps = load_existing_messages(session_id)
    added = 0
    ignored = 0

    # pagination initiale (None -> recent messages)
    timestamp_before: Optional[int] = None

    # conserve les nouveaux messages collectés (possiblement plusieurs pages)
    collected_new: List[Dict[str, object]] = []

    consecutive_all_existing_pages = 0

    # boucle de pagination : on arrête si on trouve une page sans nouveaux messages
    while True:
        resp = call_messages_api(website_id, session_id, auth, timestamp_before)
        if resp is None:
            # erreur réseau -> on arrête la conversation
            break

        if resp.status_code == 429:
            # quota atteint : on relève et renvoie None (appelant gère)
            raise RuntimeError("HTTP 429: quota d'appels atteint")

        if resp.status_code not in (200, 206):
            print(f"Réponse inattendue pour {session_id}: {resp.status_code} {resp.text}")
            break

        try:
            data = resp.json()
        except Exception:
            print(f"Impossible de décoder JSON pour {session_id}")
            break

        items = data.get("data") if isinstance(data, dict) else None
        if not items:
            # pas de messages sur cette page -> fin
            break

        page_has_new = False
        min_ts_on_page = None
        for m in items:
            fp = m.get("fingerprint")
            if not isinstance(fp, str):
                ignored += 1
                continue
            # détermination du timestamp le plus ancien de la page
            try:
                t = int(m.get("timestamp", 0))
            except Exception:
                t = 0
            if min_ts_on_page is None or t < min_ts_on_page:
                min_ts_on_page = t

            if fp in existing_fps:
                ignored += 1
                continue
            # nouveau message
            collected_new.append(m)
            existing_fps.add(fp)
            added += 1
            page_has_new = True

        # si on a ajouté des messages, on continue la pagination (pour récupérer plus ancien)
        # sinon si la page ne contient aucun nouveau message, on arrête
        if not page_has_new:
            consecutive_all_existing_pages += 1
            # si deux pages consécutives sans nouveaux, on considère qu'il n'y a plus rien à ajouter
            if consecutive_all_existing_pages >= 2:
                break
        else:
            consecutive_all_existing_pages = 0

        # préparer la prochaine page : timestamp_before = min timestamp de la page
        if min_ts_on_page is None:
            break
        timestamp_before = min_ts_on_page

        # petite pause pour éviter de frapper l'API trop vite
        time.sleep(0.1)

    # fusionner et écrire si on a de nouveaux messages
    if collected_new:
        merged = merge_and_sort_messages(existing_msgs, collected_new)
        path = MESS_DIR / f"{session_id}.jsonl"
        MESS_DIR.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for m in merged:
                f.write(json.dumps(m, ensure_ascii=False) + "\n")

    return added, ignored


def main():
    parser = argparse.ArgumentParser(description="Exporter les messages Crisp par conversation")
    parser.add_argument("--nb", type=int, default=50, help="Nombre max de conversations à traiter (défaut 50)")
    parser.add_argument("--reset", action="store_true", help="Réinitialiser le fichier d'état")
    args = parser.parse_args()

    identifier = os.getenv("CRISP_IDENTIFIER_PROD")
    key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")
    if not identifier or not key or not website_id:
        print("Les variables d'environnement CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définies.")
        sys.exit(1)

    if args.reset and STATE_FILE.exists():
        try:
            STATE_FILE.unlink()
            print("Fichier d'état réinitialisé.")
        except Exception:
            pass

    convs = read_conversations_list()
    total_convs = len(convs)
    if total_convs == 0:
        print("Aucune conversation trouvée dans", CONV_FILE)
        return

    state = load_state()
    next_index = int(state.get("next_index", 0))

    max_to_process = max(0, int(args.nb))

    processed = 0
    treated = 0
    ignored_convs = 0
    files_created_or_updated = 0

    auth = (identifier, key)

    i = next_index
    while i < total_convs and processed < max_to_process:
        conv_obj = convs[i]
        session_id = extract_session_id(conv_obj)
        if not session_id:
            print(f"Ligne {i}: session_id introuvable, ignorée.")
            ignored_convs += 1
            i += 1
            processed += 1
            state["next_index"] = i
            save_state(state)
            continue

        print(f"Traitement conversation {session_id} (index {i + 1}/{total_convs})...")
        try:
            added, ignored = process_conversation(website_id, auth, session_id)
        except RuntimeError as e:
            msg = str(e)
            if "429" in msg:
                print("Quota d'appels atteint (429). Arrêt de l'exécution.")
                break
            else:
                print(f"Erreur non gérée pour {session_id}: {e}")
                i += 1
                processed += 1
                state["next_index"] = i
                save_state(state)
                continue

        processed += 1
        treated += 1

        # Si aucun message ajouté et fichier existant -> conversation ignorée
        path = MESS_DIR / f"{session_id}.jsonl"
        if added == 0:
            if path.exists():
                print(f" - Aucun nouveau message pour {session_id} (ignorée).")
                ignored_convs += 1
            else:
                # pas de messages et pas de fichier -> créer fichier vide
                MESS_DIR.mkdir(parents=True, exist_ok=True)
                path.touch()
                files_created_or_updated += 1
                print(f" - Aucun message retourné mais fichier créé pour {session_id}.")
        else:
            files_created_or_updated += 1
            print(f" - Messages ajoutés pour {session_id}: {added} (ignorés: {ignored})")

        # Mettre à jour l'état pour reprendre plus tard
        i += 1
        state["next_index"] = i
        save_state(state)

    # Calculer nombre de fichiers .jsonl dans MESS_DIR
    num_files = 0
    if MESS_DIR.exists():
        num_files = len([p for p in MESS_DIR.iterdir() if p.is_file() and p.suffix == ".jsonl"])

    print("--- Récapitulatif ---")
    print(f"Conversations listées : {total_convs}")
    print(f"Conversations traitées lors de cette exécution: {treated}")
    print(f"Conversations ignorées lors de cette exécution: {ignored_convs}")
    print(f"Fichiers .jsonl de conversations présents dans {MESS_DIR}: {num_files}")


if __name__ == "__main__":
    main()
