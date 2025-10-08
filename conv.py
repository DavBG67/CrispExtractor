#!/usr/bin/env python3
"""
Script pour exporter les conversations d'un site Crisp vers un fichier jsonl.

Usage:
  conv.py [--nb N]

Le script lit les variables d'environnement suivantes:
  - CRISP_IDENTIFIER_PROD
  - CRISP_KEY_PROD
  - ID_SITE_CRISP

Le script maintient un fichier d'état `.state.json` pour permettre la reprise
de l'exportation (page courante). Les conversations sont stockées dans
`./conversations/conversations.jsonl` (création si nécessaire). Le fichier final
est trié par `active.last` descendant.

Commentaires en français et code lisible pour faciliter la maintenance.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

# --- Configuration des chemins ---
ROOT = Path(__file__).resolve().parent
CONV_DIR = ROOT / "conversations"
CONV_FILE = CONV_DIR / "conversations.jsonl"
STATE_FILE = ROOT / ".state.json"


def read_state(path: Path) -> Dict[str, Any]:
    """Lit le fichier d'état si présent, sinon retourne un dict vide."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_state(path: Path, state: Dict[str, Any]) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_existing_ids(path: Path) -> Set[str]:
    """Charge les ids de conversation déjà présents dans le fichier jsonl.

    Renvoie un set d'identifiants pour éviter les doublons.
    """
    ids: Set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                cid = obj.get("id") or obj.get("_id") or obj.get("conversation_id")
                if cid:
                    ids.add(str(cid))
            except Exception:
                # Ignorer les lignes invalides
                continue
    return ids


def append_conversations(path: Path, conversations: List[Dict[str, Any]]) -> None:
    """Ajoute une liste de conversations au fichier jsonl (append).
    Chaque conversation est écrite sur une ligne JSON indépendante.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for conv in conversations:
            fh.write(json.dumps(conv, ensure_ascii=False))
            fh.write("\n")


def sort_conversations_file(path: Path) -> None:
    """Trie les conversations du fichier par active.last descendant et réécrit le fichier."""
    if not path.exists():
        return
    items: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                items.append(obj)
            except Exception:
                continue

    def key_fn(c: Dict[str, Any]) -> int:
        try:
            return int(c.get("active", {}).get("last", 0) or 0)
        except Exception:
            return 0

    items.sort(key=key_fn, reverse=True)

    # Réécriture atomique
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for obj in items:
            fh.write(json.dumps(obj, ensure_ascii=False))
            fh.write("\n")
    tmp.replace(path)


def fetch_conversations_page(
    session: requests.Session,
    website_id: str,
    page: int,
    per_page: int,
    auth: tuple[str, str],
    headers: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Récupère une page de conversations depuis l'API Crisp.

    Nous utilisons une approche prudente pour parser la réponse: la liste de
    conversations peut se trouver à la racine ou dans une clé 'data'.
    """
    url = f"https://api.crisp.chat/v1/website/{website_id}/conversations/"
    params = {"page": page, "limit": per_page}
    resp = session.get(url, params=params, auth=auth, headers=headers, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        # Plusieurs formes possibles; tenter des clés connues
        for key in ("data", "conversations", "results", "items"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
        # Sinon, tenter de dériver une liste à partir du dict si possible
        # (moins probable)
        return []
    return []


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Exporter les conversations Crisp vers un fichier jsonl")
    parser.add_argument("--nb", type=int, default=400, help="Nombre maximum de nouvelles conversations à exporter (défaut: 400)")
    args = parser.parse_args(argv)

    identifier = os.getenv("CRISP_IDENTIFIER_PROD")
    key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")

    if not identifier or not key or not website_id:
        print("Les variables d'environnement CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définies.")
        return 2

    nb_to_export = max(0, args.nb)

    state = read_state(STATE_FILE)
    page = int(state.get("page", 1))

    headers = {"Content-Type": "application/json", "X-Crisp-Tier": "plugin"}
    auth = (identifier, key)

    session = requests.Session()

    existing_ids = load_existing_ids(CONV_FILE)
    total_before = len(existing_ids)

    exported_total = 0
    ignored_total = 0

    per_page_default = 100  # taille de page par défaut pour limiter les appels

    print(f"Démarrage de l'export : cible={nb_to_export}, page_de_depuis_state={page}, total_dans_fichier={total_before}")

    # Boucle de pagination
    while exported_total < nb_to_export:
        per_page = min(per_page_default, nb_to_export - exported_total)
        try:
            convs = fetch_conversations_page(session, website_id, page, per_page, auth, headers)
        except requests.HTTPError as e:
            print(f"Erreur HTTP lors de l'appel API (page={page}): {e}")
            break
        except Exception as e:
            print(f"Erreur inattendue lors de l'appel API (page={page}): {e}")
            break

        if not convs:
            print(f"Aucune conversation récupérée à la page {page}. Fin de l'export.")
            break

        exported_this_round = 0
        ignored_this_round = 0
        to_append: List[Dict[str, Any]] = []

        for conv in convs:
            cid = conv.get("id") or conv.get("_id") or conv.get("conversation_id")
            if not cid:
                # Si pas d'id, on l'ignore
                ignored_this_round += 1
                continue
            cid = str(cid)
            if cid in existing_ids:
                ignored_this_round += 1
                continue
            # Nouvelle conversation -> ajouter à la liste à écrire
            to_append.append(conv)
            existing_ids.add(cid)
            exported_this_round += 1

        # Écriture dans le fichier (append)
        if to_append:
            append_conversations(CONV_FILE, to_append)

        exported_total += exported_this_round
        ignored_total += ignored_this_round

        print(f"page={page}: exportées={exported_this_round}, ignorées={ignored_this_round}, total_fichier={len(existing_ids)}")

        # Mise à jour de l'état pour permettre la reprise
        state = {"page": page + 1}
        write_state(STATE_FILE, state)

        # Si le nombre récupéré est inférieur à per_page, on a atteint la fin
        if len(convs) < per_page:
            print("Moins de résultats que la taille de page demandée -> fin des pages disponibles.")
            break

        page += 1

    # Après la boucle : tri final du fichier
    sort_conversations_file(CONV_FILE)

    total_after = len(existing_ids)
    print("--- Récapitulatif ---")
    print(f"Exportées pendant l'exécution: {exported_total}")
    print(f"Ignorées pendant l'exécution: {ignored_total}")
    print(f"Total de conversations dans {CONV_FILE}: {total_after}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
