#!/usr/bin/env python3
"""
conv.py

Script pour exporter les conversations d'un compte Crisp dans un fichier JSONL.

Fonctionnalités :
- Pagination via page_number et per_page=20
- Gestion d'un fichier d'état pour reprendre l'export
- Dé-duplication par session_id
- Tri descendant par active.last
- Options : --nb N (nombre max de nouvelles conversations à exporter, défaut 400), --reset

Variables d'environnement attendues :
- CRISP_IDENTIFIER_PROD
- CRISP_KEY_PROD
- ID_SITE_CRISP

Le script utilise l'API Crisp : https://api.crisp.chat/v1/website/:website_id/conversations/:page_number?per_page=20

"""

import os
import sys
import time
import json
import argparse
from typing import Dict, Any, List, Optional
from pathlib import Path
import requests

# Constantes
BASE_API = "https://api.crisp.chat/v1/website/{website_id}/conversations/{page_number}?per_page=20"
CONV_DIR = Path(__file__).parent / "conversations"
CONV_FILE = CONV_DIR / "conversations.jsonl"
STATE_FILE = CONV_DIR / "conversations.jsonl.state.json"

# Headers requis
HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}


def load_state() -> Dict[str, Any]:
    """Charge l'état depuis STATE_FILE si disponible."""
    if STATE_FILE.exists():
        try:
            with STATE_FILE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state: Dict[str, Any]) -> None:
    """Sauvegarde l'état dans STATE_FILE."""
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def read_existing_conversations() -> Dict[str, Dict[str, Any]]:
    """Lit le fichier JSONL existant et renvoie un dict par session_id."""
    res = {}
    if not CONV_FILE.exists():
        return res
    with CONV_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                session_id = extract_session_id(obj)
                if session_id:
                    res[session_id] = obj
            except Exception:
                # ignore malformed lines
                continue
    return res


def extract_session_id(conv_obj: Dict[str, Any]) -> Optional[str]:
    """Extrait session_id d'un objet conversation selon la structure attendue."""
    # Selon la doc, l'identifiant est typiquement dans conv_obj.get('session_id')
    if not isinstance(conv_obj, dict):
        return None
    # Plusieurs clés possibles selon la réponse
    for key in ("session_id", "id", "_id"):
        if key in conv_obj and isinstance(conv_obj[key], str):
            return conv_obj[key]
    # Parfois il peut être sous data -> session_id
    if "data" in conv_obj and isinstance(conv_obj["data"], dict):
        s = conv_obj["data"].get("session_id")
        if isinstance(s, str):
            return s
    return None


def sort_conversations(convs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Trie les conversations par active.last descendant (timestamp)."""
    def last_ts(c: Dict[str, Any]) -> int:
        try:
            # accès à active.last
            active = c.get("active", {})
            if isinstance(active, dict):
                last = active.get("last")
                if isinstance(last, int):
                    return last
                # parfois string
                if isinstance(last, str) and last.isdigit():
                    return int(last)
        except Exception:
            pass
        return 0

    return sorted(convs, key=last_ts, reverse=True)


def call_api(website_id: str, page_number: int, auth: requests.auth.AuthBase):
    url = BASE_API.format(website_id=website_id, page_number=page_number)
    try:
        resp = requests.get(url, headers=HEADERS, auth=auth, timeout=30)
        return resp
    except requests.RequestException as e:
        print(f"Erreur réseau lors de l'appel API: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Exporter les conversations Crisp en JSONL")
    parser.add_argument("--nb", type=int, default=400, help="Nombre max de nouvelles conversations à exporter (défaut 400)")
    parser.add_argument("--reset", action="store_true", help="Supprimer le fichier conversations.jsonl et réinitialiser l'état")
    args = parser.parse_args()

    # Vérification des variables d'environnement
    identifier = os.getenv("CRISP_IDENTIFIER_PROD")
    key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")
    if not identifier or not key or not website_id:
        print("Les variables d'environnement CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définies.")
        sys.exit(1)

    # Prépare dossier
    CONV_DIR.mkdir(parents=True, exist_ok=True)

    if args.reset:
        if CONV_FILE.exists():
            CONV_FILE.unlink()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        print("Fichiers de conversations et d'état supprimés. Reprise depuis le début.")

    # Auth HTTP Basic (Identifier:Key)
    auth = (identifier, key)

    # Load existing conversations
    existing = read_existing_conversations()
    existing_count_initial = len(existing)

    # Load or init state
    state = load_state()
    page_number = int(state.get("next_page", 1))

    exported = 0
    ignored = 0
    total_added_this_run = 0

    # Ouvrir le fichier en mode append
    convs_to_write = []

    target_nb = args.nb

    # Loop jusqu'à atteindre target_nb ou plus d'items
    while exported < target_nb:
        print(f"Appel API page {page_number} ... (exportés: {exported}, ignorés: {ignored})")
        resp = call_api(website_id, page_number, auth)
        if resp is None:
            print("Échec de l'appel API, arrêt.")
            break

        if resp.status_code == 429:
            # quota atteint
            print("Réponse 429: quota d'appels atteint. Arrêt prématuré.")
            break

        if resp.status_code not in (200, 206):
            print(f"Réponse inattendue de l'API: {resp.status_code} {resp.text}")
            break

        try:
            data = resp.json()
        except Exception:
            print("Impossible de décoder la réponse JSON, arrêt.")
            break

        # La réponse devrait contenir 'data' : liste
        page_items = data.get("data") if isinstance(data, dict) else None
        if not page_items:
            print("Aucun résultat sur cette page, fin de la récupération.")
            break

        new_found = 0
        for item in page_items:
            session_id = None
            # L'item peut être une structure contenant 'session_id' ou 'session' etc.
            if isinstance(item, dict):
                session_id = extract_session_id(item)
            if not session_id:
                ignored += 1
                continue
            if session_id in existing:
                ignored += 1
                continue
            # Ajout
            existing[session_id] = item
            convs_to_write.append(item)
            exported += 1
            new_found += 1
            if exported >= target_nb:
                break

        if new_found > 0:
            # Sauver les nouvelles conversations dans le fichier (append)
            with CONV_FILE.open("a", encoding="utf-8") as f:
                for c in convs_to_write:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")
            total_added_this_run += len(convs_to_write)
            convs_to_write = []
            # Trier et réécrire l'ensemble du fichier selon last desc et en s'assurant d'uniques
            all_convs = list(existing.values())
            sorted_convs = sort_conversations(all_convs)
            with CONV_FILE.open("w", encoding="utf-8") as f:
                for c in sorted_convs:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")

        # Mettre à jour l'état
        page_number += 1
        state["next_page"] = page_number
        save_state(state)

        # Si moins que per_page renvoyé, fin
        try:
            if isinstance(page_items, list) and len(page_items) < 20:
                print("Dernière page atteinte (moins de 20 items).")
                break
        except Exception:
            pass

        # Petite pause pour respecter quota
        time.sleep(0.2)

    # Rapport final
    final_total = len(existing)
    print("--- Récapitulatif ---")
    print(f"Conversations initialement présentes: {existing_count_initial}")
    print(f"Nouvelles conversations exportées lors de cette exécution: {total_added_this_run}")
    print(f"Conversations ignorées lors de cette exécution: {ignored}")
    print(f"Conversations totales dans le fichier: {final_total}")


if __name__ == "__main__":
    main()
