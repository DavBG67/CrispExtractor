#!/usr/bin/env python3
"""Exporteur de conversations Crisp vers un fichier JSONL.

Usage:
  python conv.py [--nb N] [--reset]

Le script lit les variables d'environnement:
  - CRISP_IDENTIFIER_PROD
  - CRISP_KEY_PROD
  - ID_SITE_CRISP

Il conserve l'état de pagination dans conversations/conversations.jsonl.state.json
et stocke les conversations dans conversations/conversations.jsonl
"""

import os
import sys
import json
import time
import argparse
from typing import Dict, Any, Tuple, List

import requests

# Constantes
CONV_DIR = os.path.join(os.path.dirname(__file__), "conversations")
CONV_FILE = os.path.join(CONV_DIR, "conversations.jsonl")
STATE_FILE = os.path.join(CONV_DIR, "conversations.jsonl.state.json")
API_URL_TEMPLATE = "https://api.crisp.chat/v1/website/{website_id}/conversations/"
HEADERS = {"Content-Type": "application/json", "X-Crisp-Tier": "plugin"}


def load_state() -> Dict[str, Any]:
    """Charge le fichier d'état s'il existe, sinon retourne un état par défaut."""
    if not os.path.exists(STATE_FILE):
        return {"page": 0, "cursor": None}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {"page": 0, "cursor": None}


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)


def read_existing_sessions() -> Dict[str, Dict[str, Any]]:
    """Lit le fichier conversations.jsonl et renvoie un dict session_id -> obj.
    Les conversations sans session_id sont ignorées pour l'unicité.
    """
    sessions = {}
    if not os.path.exists(CONV_FILE):
        return sessions
    with open(CONV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            sid = obj.get("session_id")
            if sid:
                sessions[sid] = obj
    return sessions


def write_conversations_incremental(objs: List[Dict[str, Any]]) -> None:
    """Ajoute des conversations au fichier jsonl (append)."""
    os.makedirs(CONV_DIR, exist_ok=True)
    with open(CONV_FILE, "a", encoding="utf-8") as f:
        for o in objs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")


def fetch_conversations(website_id: str, limit: int = 50, cursor: str = None, auth: Tuple[str, str] = None):
    """Appelle l'API Crisp pour récupérer une page de conversations.

    Retourne (ok: bool, data: dict_or_none, next_cursor: str_or_none)
    """
    url = API_URL_TEMPLATE.format(website_id=website_id)
    params = {"limit": limit}
    if cursor:
        params["cursor"] = cursor
    try:
        resp = requests.get(url, headers=HEADERS, params=params, auth=auth, timeout=30)
    except Exception as e:
        print(f"Erreur réseau lors de l'appel API: {e}")
        return False, None, None

    if resp.status_code != 200:
        print(f"API Crisp a répondu: {resp.status_code} - {resp.text}")
        return False, None, None

    try:
        payload = resp.json()
    except Exception as e:
        print(f"Impossible de décoder la réponse JSON: {e}")
        return False, None, None

    # l'API retourne 'data' et parfois 'cursor' (ou 'next' selon la doc)
    data = payload.get("data", [])
    next_cursor = payload.get("cursor") or payload.get("next")
    return True, data, next_cursor


def parse_args():
    p = argparse.ArgumentParser(description="Exporter les conversations Crisp vers un fichier jsonl")
    p.add_argument("--nb", type=int, default=400, help="Nombre maximum de nouvelles conversations à exporter (défaut 400)")
    p.add_argument("--reset", action="store_true", help="Réinitialiser le fichier de conversations et l'état")
    return p.parse_args()


def main():
    args = parse_args()

    # Vérification variables d'environnement
    identifier = os.environ.get("CRISP_IDENTIFIER_PROD")
    key = os.environ.get("CRISP_KEY_PROD")
    website_id = os.environ.get("ID_SITE_CRISP")

    if not identifier or not key or not website_id:
        print("Veuillez définir CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP dans l'environnement.")
        sys.exit(1)

    # Auth: Crisp uses basic auth with identifier:key
    auth = (identifier, key)

    # Reset si demandé
    if args.reset:
        if os.path.exists(CONV_FILE):
            os.remove(CONV_FILE)
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        print("Fichiers de conversations et d'état réinitialisés.")

    state = load_state()

    existing = read_existing_sessions()
    total_before = len(existing)

    max_new = args.nb
    per_call = 50

    # Si l'utilisateur demande moins que la taille par défaut, adapter
    if max_new < per_call:
        per_call = max_new

    exported_total = 0
    ignored_total = 0

    cursor = state.get("cursor")

    # Boucle principale
    while exported_total < max_new:
        remaining = max_new - exported_total
        limit = min(per_call, remaining)

        ok, data, next_cursor = fetch_conversations(website_id, limit=limit, cursor=cursor, auth=auth)
        if not ok:
            print("Arrêt de l'export suite à une erreur API.")
            break

        if not data:
            print("Aucune conversation retournée par l'API. Fin de l'export.")
            break

        to_write = []
        exported_this_round = 0
        ignored_this_round = 0

        for item in data:
            # Chaque item représente une conversation selon la doc
            sid = item.get("session_id")
            if not sid:
                # pas de session_id => on ignore pour éviter les doublons
                ignored_this_round += 1
                continue
            if sid in existing:
                ignored_this_round += 1
                continue
            # Nouvel élément
            to_write.append(item)
            existing[sid] = item
            exported_this_round += 1

        # Ajouter au fichier
        if to_write:
            write_conversations_incremental(to_write)

        exported_total += exported_this_round
        ignored_total += ignored_this_round

        # Mise à jour de l'état
        cursor = next_cursor
        state["cursor"] = cursor
        save_state(state)

        # Tri du fichier final si on a atteint la limite ou pas de cursor
        total_now = len(existing)

        print(f"Tour: exportées={exported_this_round} ; ignorées={ignored_this_round} ; total_fichier={total_now}")

        if not cursor:
            # plus de pages
            break

        # petite pause pour respecter le quota
        time.sleep(0.1)

    # À la fin: trier le fichier selon active.last descendant
    try:
        # Recharger toutes les conversations depuis le fichier puis trier
        all_objs = []
        if os.path.exists(CONV_FILE):
            with open(CONV_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        all_objs.append(json.loads(line))
                    except Exception:
                        continue

        def last_active(o: Dict[str, Any]):
            # Défaut 0 si absent
            return o.get("active", {}).get("last") or 0

        all_objs.sort(key=last_active, reverse=True)

        # Réécrire le fichier trié
        with open(CONV_FILE, "w", encoding="utf-8") as f:
            for o in all_objs:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")

        print("--- Récapitulatif ---")
        print(f"Exportées pendant l'exécution: {exported_total}")
        print(f"Ignorées pendant l'exécution: {ignored_total}")
        print(f"Total conversations dans le fichier: {len(all_objs)}")
    except Exception as e:
        print(f"Erreur lors du tri/écriture final: {e}")


if __name__ == "__main__":
    main()
