#!/usr/bin/env python3
"""
Script pour exporter les conversations d'un compte Crisp vers un fichier jsonl.

Fonctionnalités:
- Pagination via page_number et per_page=20
- Gestion d'un fichier d'état pour reprendre la pagination
- Déduplication par session_id
- Options CLI: --nb (nombre max de nouvelles conversations), --reset

Variables d'environnement attendues:
- CRISP_IDENTIFIER_PROD: identifiant (user) pour l'API Crisp
- CRISP_KEY_PROD: clé (password) pour l'API Crisp
- ID_SITE_CRISP: website_id

Le script écrit dans le répertoire `conversations/` les fichiers:
- conversations.jsonl
- conversations.jsonl.state.json

Commentaires en français.
"""
import os
import sys
import json
import time
import argparse
from typing import Dict, Any, List, Tuple, Set

import requests

CONV_DIR = os.path.join(os.path.dirname(__file__), "conversations")
CONV_FILE = os.path.join(CONV_DIR, "conversations.jsonl")
STATE_FILE = os.path.join(CONV_DIR, "conversations.jsonl.state.json")

HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}

PER_PAGE = 20
DEFAULT_MAX = 400


def load_state() -> Dict[str, Any]:
    """Charge l'état de pagination si présent."""
    if not os.path.exists(STATE_FILE):
        return {"next_page": 1}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"next_page": 1}


def save_state(state: Dict[str, Any]) -> None:
    os.makedirs(CONV_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def load_existing_session_ids() -> Set[str]:
    """Lit le fichier jsonl existant et renvoie l'ensemble des session_id déjà présents."""
    ids = set()
    if not os.path.exists(CONV_FILE):
        return ids
    try:
        with open(CONV_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    sid = extract_session_id(obj)
                    if sid:
                        ids.add(sid)
                except Exception:
                    continue
    except Exception:
        pass
    return ids


def extract_session_id(conv: Dict[str, Any]) -> str:
    """Extrait le session_id d'une conversation (ou None si absent)."""
    # Selon la doc, l'identifiant se trouve dans data[].session_id
    # L'objet passé est une entrée de la liste `data` retournée par l'API
    return conv.get("session_id") or conv.get("session") or None


def write_conversations_to_file(convs: List[Dict[str, Any]]) -> None:
    """Ajoute des conversations à la fin du fichier jsonl (une par ligne)."""
    os.makedirs(CONV_DIR, exist_ok=True)
    with open(CONV_FILE, "a", encoding="utf-8") as fh:
        for c in convs:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")


def read_all_conversations() -> List[Dict[str, Any]]:
    """Lit toutes les conversations depuis le fichier jsonl et les retourne en liste d'objets."""
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


def sort_conversations_by_last_active(convs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Trie les conversations selon active.last (descendant)."""
    def key_fn(c):
        try:
            return int(c.get("active", {}).get("last", 0) or 0)
        except Exception:
            return 0
    return sorted(convs, key=key_fn, reverse=True)


def call_crisp_api(website_id: str, page_number: int, auth: Tuple[str, str]) -> requests.Response:
    url = f"https://api.crisp.chat/v1/website/{website_id}/conversations/{page_number}?per_page={PER_PAGE}"
    resp = requests.get(url, headers=HEADERS, auth=auth, timeout=30)
    return resp


def main(argv=None):
    parser = argparse.ArgumentParser(description="Exporter toutes les conversations Crisp pour un site donné vers conversations/conversations.jsonl")
    parser.add_argument("--nb", type=int, default=DEFAULT_MAX, help=f"Nombre maximum de nouvelles conversations à exporter (défaut {DEFAULT_MAX})")
    parser.add_argument("--reset", action="store_true", help="Réinitialiser le fichier de conversations et l'état")
    args = parser.parse_args(argv)

    max_to_export = args.nb

    # Récupération des variables d'environnement
    crisp_id = os.getenv("CRISP_IDENTIFIER_PROD")
    crisp_key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")

    if not (crisp_id and crisp_key and website_id):
        print("ERREUR: variables d'environnement CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définies.")
        sys.exit(2)

    auth = (crisp_id, crisp_key)

    if args.reset:
        # suppression des fichiers
        try:
            if os.path.exists(CONV_FILE):
                os.remove(CONV_FILE)
            if os.path.exists(STATE_FILE):
                os.remove(STATE_FILE)
            print("Réinitialisation: fichiers conversations et état supprimés.")
        except Exception as e:
            print(f"Impossible de réinitialiser: {e}")

    state = load_state()
    next_page = int(state.get("next_page", 1) or 1)

    existing_ids = load_existing_session_ids()

    total_exported = 0
    total_ignored = 0

    # boucle principale
    while True:
        # Arrêt si on a atteint la limite souhaitée
        if total_exported >= max_to_export:
            break

        print(f"Appel API: page {next_page} (per_page={PER_PAGE})")
        try:
            resp = call_crisp_api(website_id, next_page, auth)
        except requests.RequestException as e:
            print(f"Erreur réseau lors de l'appel API: {e}")
            break

        if resp.status_code == 429:
            print("Quota API atteint (429). Arrêt de l'exportation.")
            break

        if resp.status_code not in (200, 206):
            print(f"Réponse API inattendue: {resp.status_code}. Contenu: {resp.text[:200]}")
            break

        try:
            payload = resp.json()
        except Exception:
            print("Réponse API non JSON. Arrêt.")
            break

        data = payload.get("data") or []
        if not data:
            print("Aucun résultat pour cette page. Fin de l'export.")
            break

        # filtrage des conversations valides et déduplication
        new_convs = []
        exported_this_round = 0
        ignored_this_round = 0
        for item in data:
            sid = extract_session_id(item)
            if not sid:
                ignored_this_round += 1
                continue
            if sid in existing_ids:
                ignored_this_round += 1
                continue
            new_convs.append(item)
            existing_ids.add(sid)
            exported_this_round += 1
            total_exported += 1
            # Stop early if we've reached the requested max
            if total_exported >= max_to_export:
                break

        # écrire les nouvelles conversations dans le fichier
        if new_convs:
            write_conversations_to_file(new_convs)

        total_ignored += ignored_this_round

        # affichages lisibles pour cette itération
        total_in_file = len(load_existing_session_ids())
        print(f"Page {next_page}: exportées={exported_this_round}, ignorées={ignored_this_round}, total_fichier={total_in_file}")

        # sauvegarde de l'état (prochaine page)
        next_page += 1
        save_state({"next_page": next_page})

        # Si le serveur a renvoyé moins que per_page, on peut considérer qu'on est arrivé en fin
        if len(data) < PER_PAGE:
            print("Moins de résultats que per_page -> fin des pages disponibles.")
            break

        # Pause légère pour ne pas surcharger l'API
        time.sleep(0.2)

    # A la fin, on doit trier le fichier selon active.last (descendant)
    all_convs = read_all_conversations()
    sorted_convs = sort_conversations_by_last_active(all_convs)
    # réécrire le fichier trié
    os.makedirs(CONV_DIR, exist_ok=True)
    with open(CONV_FILE, "w", encoding="utf-8") as fh:
        for c in sorted_convs:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    final_total = len(sorted_convs)
    print("--- Résumé ---")
    print(f"Total exportées lors de l'exécution: {total_exported}")
    print(f"Total ignorées lors de l'exécution: {total_ignored}")
    print(f"Total conversations dans le fichier: {final_total}")


if __name__ == "__main__":
    main()
