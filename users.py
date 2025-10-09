#!/usr/bin/env python3
"""
Script pour constituer/maintenir le fichier `utilisateurs/utilisateurs.jsonl`
à partir des conversations et de l'API Crisp (people profile).

Fonctionnalités principales:
- Parcourt `conversations/conversations.jsonl` et extrait les emails (cherche dans data.meta.email ou meta.email)
- Pour chaque email qui n'est pas déjà dans `utilisateurs/utilisateurs.jsonl`, appelle l'API Crisp people/profile
- Gère les codes 200 et 206 comme valides, traite 429 avec retries/backoff
- Evite les doublons (par email)
- Tri final du fichier par ordre alphabétique des emails

Variables d'environnement attendues:
- CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD, ID_SITE_CRISP

Usage:
python users.py --nb 50 [--reset]
"""
import os
import sys
import json
import time
import argparse
from typing import List, Dict, Any, Set, Tuple

import requests

BASE_DIR = os.path.dirname(__file__)
CONV_DIR = os.path.join(BASE_DIR, "conversations")
CONV_FILE = os.path.join(CONV_DIR, "conversations.jsonl")

USERS_DIR = os.path.join(BASE_DIR, "utilisateurs")
USERS_FILE = os.path.join(USERS_DIR, "utilisateurs.jsonl")

HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}

DEFAULT_NB = 50


def extract_email(conv: Dict[str, Any]) -> str:
    """Tente d'extraire un email d'une conversation.

    On accepte plusieurs formes: conv.get('meta', {}).get('email') ou
    conv.get('data', {}).get('meta', {}).get('email').
    Retourne l'email en minuscules ou None.
    """
    if not isinstance(conv, dict):
        return None
    # cas commun utilisé dans les fixtures
    email = None
    try:
        email = conv.get("meta", {}).get("email")
    except Exception:
        email = None
    if not email:
        try:
            email = conv.get("data", {}).get("meta", {}).get("email")
        except Exception:
            email = None
    if email and isinstance(email, str):
        return email.strip().lower()
    return None


def load_existing_users_emails() -> Set[str]:
    """Lit `USERS_FILE` et retourne l'ensemble des emails déjà présents."""
    out = set()
    if not os.path.exists(USERS_FILE):
        return out
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    e = obj.get("email")
                    if e:
                        out.add(e.strip().lower())
                except Exception:
                    continue
    except Exception:
        pass
    return out


def call_crisp_people_profile(website_id: str, people_id: str, auth: Tuple[str, str]) -> requests.Response:
    """Appelle l'endpoint people/profile pour un people_id donné (ici l'email).

    Cette fonction est séparée pour faciliter le test (mock).
    """
    url = f"https://api.crisp.chat/v1/website/{website_id}/people/profile/{people_id}"
    return requests.get(url, headers=HEADERS, auth=auth, timeout=30)


def write_users_to_file(users: List[Dict[str, Any]]) -> None:
    os.makedirs(USERS_DIR, exist_ok=True)
    with open(USERS_FILE, "a", encoding="utf-8") as fh:
        for u in users:
            fh.write(json.dumps(u, ensure_ascii=False) + "\n")


def read_all_users() -> List[Dict[str, Any]]:
    if not os.path.exists(USERS_FILE):
        return []
    out = []
    with open(USERS_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def sort_users_by_email(users: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def key_fn(u):
        try:
            return (u.get("email") or "").lower()
        except Exception:
            return ""

    return sorted(users, key=key_fn)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Exporter les utilisateurs à partir des conversations et de l'API Crisp")
    parser.add_argument("--nb", type=int, default=DEFAULT_NB, help=f"Nombre maximum d'utilisateurs à exporter (défaut {DEFAULT_NB})")
    parser.add_argument("--reset", action="store_true", help="Réinitialiser le fichier utilisateurs avant d'exporter")
    args = parser.parse_args(argv)

    max_to_export = int(args.nb or DEFAULT_NB)

    crisp_id = os.getenv("CRISP_IDENTIFIER_PROD")
    crisp_key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")

    if not (crisp_id and crisp_key and website_id):
        print("ERREUR: CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définis.")
        sys.exit(2)

    auth = (crisp_id, crisp_key)

    if args.reset:
        try:
            if os.path.exists(USERS_FILE):
                os.remove(USERS_FILE)
                print(f"Fichier {USERS_FILE} supprimé (reset).")
        except Exception as e:
            print(f"Impossible de supprimer {USERS_FILE}: {e}")

    # lire conversations
    if not os.path.exists(CONV_FILE):
        print(f"Aucun fichier de conversations trouvé: {CONV_FILE}")
        return

    try:
        with open(CONV_FILE, "r", encoding="utf-8") as fh:
            convs = [json.loads(l) for l in fh if l.strip()]
    except Exception as e:
        print(f"Impossible de lire {CONV_FILE}: {e}")
        return

    existing_emails = load_existing_users_emails()

    to_export = []
    exported_count = 0

    for conv in convs:
        if exported_count >= max_to_export:
            break
        email = extract_email(conv)
        if not email:
            continue
        if email in existing_emails:
            continue

        # appel API
        retries = 0
        max_retries = 5
        backoff = 1.0
        resp = None
        while retries <= max_retries:
            try:
                resp = call_crisp_people_profile(website_id, email, auth)
            except requests.RequestException as e:
                print(f"Erreur réseau lors de l'appel people/profile pour {email}: {e}")
                resp = None

            if resp is None:
                retries += 1
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 429:
                # quota atteint -> backoff et retry limité
                retries += 1
                print(f"429 reçu pour {email} (quota). retry {retries}/{max_retries} après {backoff}s")
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code in (200, 206):
                try:
                    payload = resp.json()
                except Exception:
                    print(f"Réponse non JSON pour {email}. Skip.")
                    payload = None
                if payload:
                    # s'assurer qu'il y a une clé email au premier niveau
                    if not payload.get("email"):
                        # si l'API retourne l'email ailleurs, on tente d'enrichir
                        payload["email"] = email
                    to_export.append(payload)
                    existing_emails.add(email)
                    exported_count += 1
                break

            else:
                print(f"Réponse inattendue {resp.status_code} pour {email}. Contenu: {getattr(resp, 'text', '')[:200]}")
                break

        # fin boucle retries

    # écrire les nouveaux utilisateurs
    if to_export:
        write_users_to_file(to_export)

    # trier le fichier final par email
    all_users = read_all_users()
    sorted_users = sort_users_by_email(all_users)
    os.makedirs(USERS_DIR, exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as fh:
        for u in sorted_users:
            fh.write(json.dumps(u, ensure_ascii=False) + "\n")

    print(f"Export terminé. Utilisateurs ajoutés: {exported_count}. Total fichier: {len(sorted_users)}")


if __name__ == "__main__":
    main()
