#!/usr/bin/env python3
"""
Script pour constituer/maintenir un fichier JSONL d'utilisateurs à partir des conversations
et de l'API Crisp (people profile).

Fonctionnement:
- Parcourt `conversations/conversations.jsonl` et récupère les emails dans `meta.email`.
- Vérifie si l'email est déjà présent dans `utilisateurs/utilisateurs.jsonl`.
- Si absent, appelle l'API Crisp: /website/:website_id/people/profile/:people_id
  en utilisant l'email comme people_id.
- Gère les codes 200 et 206 comme valides. Gère 429 (quota) proprement.
- Options CLI: --nb (nombre max d'utilisateurs à exporter, défaut 50), --reset (efface le fichier utilisateurs au départ).

Variables d'environnement attendues:
- CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD, ID_SITE_CRISP

Headers utilisés: Content-Type: application/json, X-Crisp-Tier: plugin

Commentaires en français et code lisible.
"""
import os
import sys
import json
import argparse
import time
from typing import Set, Dict, Any, Tuple

import requests

ROOT_DIR = os.path.dirname(__file__)
CONV_DIR = os.path.join(ROOT_DIR, "conversations")
CONV_FILE = os.path.join(CONV_DIR, "conversations.jsonl")

USERS_DIR = os.path.join(ROOT_DIR, "utilisateurs")
USERS_FILE = os.path.join(USERS_DIR, "utilisateurs.jsonl")

HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}

DEFAULT_NB = 50


def load_existing_users_emails(path: str) -> Set[str]:
    """Lit le fichier utilisateurs.jsonl et retourne l'ensemble des emails déjà présents (en minuscule)."""
    emails = set()
    if not os.path.exists(path):
        return emails
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                # l'email peut être à plusieurs endroits, la doc montre meta.email
                e = None
                if isinstance(obj, dict):
                    # la réponse people profile contient souvent 'email' au top-level ou dans 'data' / 'meta'
                    e = obj.get('email') or (obj.get('person') or {}).get('email') if obj.get('person') else None
                    if not e:
                        e = obj.get('data', {}).get('meta', {}).get('email') if isinstance(obj.get('data'), dict) else None
                if e:
                    emails.add(e.lower())
    except Exception:
        # Si le fichier est corrompu, on ignore et on repart de vide
        return set()
    return emails


def read_conversations_emails(conv_path: str) -> Set[str]:
    """Lit le fichier conversations.jsonl et retourne l'ensemble des emails trouvés dans meta.email."""
    out = set()
    if not os.path.exists(conv_path):
        return out
    with open(conv_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            email = None
            meta = obj.get("meta") if isinstance(obj, dict) else None
            if isinstance(meta, dict):
                email = meta.get("email")
                # parfois email dans meta.data.meta.email (rare)
                if not email:
                    email = meta.get("data", {}).get("meta", {}).get("email") if isinstance(meta.get("data"), dict) else None
            if email:
                out.add(email.lower())
    return out


def call_crisp_people_profile(website_id: str, people_id: str, auth: Tuple[str, str]) -> requests.Response:
    """Appelle l'API Crisp pour récupérer le profile d'un people_id (ici l'email généralement).
    Retourne l'objet Response pour permettre la gestion des codes en appelant le status_code et json().
    """
    url = f"https://api.crisp.chat/v1/website/{website_id}/people/profile/{people_id}"
    resp = requests.get(url, headers=HEADERS, auth=auth, timeout=30)
    return resp


def append_user_to_file(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_all_users(path: str) -> list:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def sort_users_by_email(users: list) -> list:
    return sorted(users, key=lambda u: (u.get('email') or '').lower())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Constitue/maintient utilisateurs/utilisateurs.jsonl à partir des conversations et Crisp API")
    parser.add_argument("--nb", type=int, default=DEFAULT_NB, help=f"Nombre max d'utilisateurs à exporter (défaut {DEFAULT_NB})")
    parser.add_argument("--reset", action="store_true", help="Réinitialiser le fichier utilisateurs avant de commencer")
    args = parser.parse_args(argv)

    max_to_export = int(args.nb or DEFAULT_NB)

    # variables d'environnement
    crisp_id = os.getenv("CRISP_IDENTIFIER_PROD")
    crisp_key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")

    if not (crisp_id and crisp_key and website_id):
        print("ERREUR: CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définies.")
        sys.exit(2)

    auth = (crisp_id, crisp_key)

    if args.reset:
        # suppression du fichier utilisateurs
        try:
            if os.path.exists(USERS_FILE):
                os.remove(USERS_FILE)
                print("Fichier utilisateurs supprimé (reset).")
        except Exception as e:
            print(f"Impossible de supprimer {USERS_FILE}: {e}")

    existing_emails = load_existing_users_emails(USERS_FILE)
    conv_emails = read_conversations_emails(CONV_FILE)

    # on va itérer les emails extraits depuis les conversations
    to_process = [e for e in sorted(conv_emails) if e not in existing_emails]
    total_added = 0

    for email in to_process:
        if total_added >= max_to_export:
            break
        # appel API Crisp
        try:
            resp = call_crisp_people_profile(website_id, email, auth)
        except requests.RequestException as e:
            print(f"Erreur réseau lors de l'appel API pour {email}: {e}")
            # ne pas finir l'exécution; on passe au suivant
            continue

        if resp.status_code == 429:
            print("Quota API atteint (429). Arrêt de l'export utilisateur.")
            break

        if resp.status_code not in (200, 206):
            print(f"Réponse API inattendue pour {email}: {resp.status_code}. Contenu: {resp.text[:200]}")
            continue

        # parse JSON
        try:
            payload = resp.json()
        except Exception:
            print(f"Réponse non JSON pour {email}. Ignoré.")
            continue

        # On écrit l'objet tel quel dans le fichier utilisateurs.jsonl
        append_user_to_file(USERS_FILE, payload)
        existing_emails.add(email)
        total_added += 1
        print(f"Ajouté: {email}")

        # petite pause pour éviter d'enchainer trop vite
        time.sleep(0.1)

    # À la fin, on trie le fichier utilisateurs.jsonl par email
    users_all = load_all_users(USERS_FILE)
    sorted_users = sort_users_by_email(users_all)
    # réécriture triée
    if sorted_users:
        os.makedirs(USERS_DIR, exist_ok=True)
        with open(USERS_FILE, "w", encoding="utf-8") as fh:
            for u in sorted_users:
                fh.write(json.dumps(u, ensure_ascii=False) + "\n")

    print(f"--- Résumé ---\nTotal ajoutés lors de l'exécution: {total_added}\nTotal utilisateurs dans le fichier: {len(sorted_users)}")


if __name__ == "__main__":
    main()
