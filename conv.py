#!/usr/bin/env python3
"""
Script pour exporter les conversations d'un compte Crisp (site) vers un fichier jsonl.

Fonctionnalités principales:
- Récupère des conversations via l'API Crisp
- Écrit les conversations nouvelles dans conversations/conversations.jsonl
- Gère un fichier d'état pour la pagination: conversations/conversations.jsonl.state.json
- Paramètres: --nb (nombre max de nouvelles conversations à exporter), --reset

Commentaires en français et code structuré pour faciliter les tests.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Set

import requests
from requests.auth import HTTPBasicAuth


CONV_DIR = Path("conversations")
CONV_FILE = CONV_DIR / "conversations.jsonl"
STATE_FILE = CONV_DIR / "conversations.jsonl.state.json"


def load_state() -> Dict[str, Any]:
    """Charge le fichier d'état s'il existe, sinon renvoie l'état par défaut."""
    if not STATE_FILE.exists():
        return {"offset": 0}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {"offset": 0}


def save_state(state: Dict[str, Any]) -> None:
    """Sauvegarde l'état actuel de la pagination."""
    CONV_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)


def load_existing_session_ids() -> Set[str]:
    """Lit le fichier jsonl existant et retourne l'ensemble des session_id reconnus."""
    ids: Set[str] = set()
    if not CONV_FILE.exists():
        return ids
    with open(CONV_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                # ignore malformed lines
                continue
            sid = extract_session_id(obj)
            if sid:
                ids.add(sid)
    return ids


def extract_session_id(conv: Dict[str, Any]) -> Optional[str]:
    """Extrait le session_id d'une conversation si possible.

    On vérifie les emplacements possibles: 'session_id' top-level, ou 'session'->'session_id'.
    """
    if not isinstance(conv, dict):
        return None
    sid = conv.get("session_id")
    if sid:
        return str(sid)
    session = conv.get("session")
    if isinstance(session, dict):
        sid2 = session.get("session_id")
        if sid2:
            return str(sid2)
    return None


def sort_conv_file() -> None:
    """Trie le fichier conversations.jsonl par active.last descendant et le réécrit."""
    if not CONV_FILE.exists():
        return
    items: List[Dict[str, Any]] = []
    with open(CONV_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                items.append(obj)
            except Exception:
                continue

    def key_active(conv: Dict[str, Any]) -> int:
        try:
            return int(conv.get("active", {}).get("last", 0) or 0)
        except Exception:
            return 0

    items.sort(key=key_active, reverse=True)

    # Réécriture complète du fichier trié
    with open(CONV_FILE, "w", encoding="utf-8") as fh:
        for obj in items:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def fetch_conversations(website_id: str, auth: HTTPBasicAuth, limit: int, offset: int) -> Dict[str, Any]:
    """Fait un appel GET vers l'API Crisp pour récupérer des conversations.

    Retourne le JSON décodé si le code HTTP est 200 ou 206. Lance une exception sinon.
    """
    url = f"https://api.crisp.chat/v1/website/{website_id}/conversations/"
    headers = {"Content-Type": "application/json", "X-Crisp-Tier": "plugin"}
    params = {"limit": limit, "offset": offset}
    resp = requests.get(url, headers=headers, params=params, auth=auth, timeout=30)
    if resp.status_code in (200, 206):
        return resp.json()
    else:
        # propagate an informative error
        resp.raise_for_status()


def run(argv: Optional[List[str]] = None) -> int:
    """Point d'entrée principal, retourne 0 en cas de succès.

    argv: liste d'arguments (optionnel) pour faciliter les tests.
    """
    parser = argparse.ArgumentParser(description="Exporter les conversations Crisp vers un jsonl")
    parser.add_argument("--nb", type=int, default=400, help="Nombre maximal de nouvelles conversations à exporter")
    parser.add_argument("--reset", action="store_true", help="Supprimer les fichiers et repartir de zéro")
    args = parser.parse_args(argv)

    # Lire les variables d'environnement nécessaires
    identifier = os.environ.get("CRISP_IDENTIFIER_PROD")
    key = os.environ.get("CRISP_KEY_PROD")
    website_id = os.environ.get("ID_SITE_CRISP")
    if not all([identifier, key, website_id]):
        print("Erreur: veuillez définir CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP dans l'environnement")
        return 2

    # Reset si demandé
    if args.reset:
        if CONV_FILE.exists():
            CONV_FILE.unlink()
        if STATE_FILE.exists():
            STATE_FILE.unlink()
        print("Fichiers de conversation et d'état supprimés (reset).")

    state = load_state()
    offset = int(state.get("offset", 0) or 0)

    existing_ids = load_existing_session_ids()
    total_in_file = len(existing_ids)

    auth = HTTPBasicAuth(identifier, key)

    to_export = max(0, int(args.nb))
    exported_total = 0
    ignored_total = 0

    # Boucle d'export
    while exported_total < to_export:
        remaining = to_export - exported_total
        limit = 50 if remaining >= 50 else remaining
        if limit <= 0:
            break

        print(f"Appel API: limit={limit} offset={offset}")
        try:
            payload = fetch_conversations(website_id, auth, limit, offset)
        except Exception as exc:
            print(f"Erreur lors de l'appel API: {exc}")
            break

        items = payload.get("data") or []
        if not items:
            print("Aucune conversation récupérée par l'API (plus de pages).")
            break

        exported_this = 0
        ignored_this = 0

        # S'assurer que le dossier existe
        CONV_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONV_FILE, "a", encoding="utf-8") as fh:
            for item in items:
                sid = extract_session_id(item)
                if not sid:
                    ignored_this += 1
                    ignored_total += 1
                    continue
                if sid in existing_ids:
                    ignored_this += 1
                    ignored_total += 1
                    continue
                # Ecrire la conversation nouvelle
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
                existing_ids.add(sid)
                exported_this += 1
                exported_total += 1
                total_in_file += 1
                if exported_total >= to_export:
                    break

        # Mise à jour offset et état
        offset += len(items)
        save_state({"offset": offset})

        print(f"Tour: exportées={exported_this} ignorées={ignored_this} | total fichier={total_in_file}")

        # Si le nombre d'éléments retournés est inférieur à la limite demandée, il n'y a plus de pages
        if len(items) < limit:
            break

    # Tri final du fichier
    sort_conv_file()

    print("--- Récapitulatif ---")
    print(f"Total exportées pendant l'exécution: {exported_total}")
    print(f"Total ignorées pendant l'exécution: {ignored_total}")
    print(f"Total conversations dans le fichier: {len(existing_ids)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
