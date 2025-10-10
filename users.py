#!/usr/bin/env python3
"""
users.py

Script pour exporter les profils utilisateurs depuis l'API Crisp.

Comportement:
- Lit les conversations dans `conversations/conversations.jsonl` et extrait les emails
- Vérifie si l'utilisateur est déjà présent dans `utilisateurs/utilisateurs.jsonl`
- Appelle l'API Crisp pour récupérer le profil si absent et l'ajoute au fichier
- Gère --nb (nombre max d'utilisateurs à récupérer, défaut 50) et --reset
- Affiche la progression (email traité) et un récapitulatif final

Variables d'environnement attendues:
- CRISP_IDENTIFIER_PROD (identifiant)
- CRISP_KEY_PROD (clé)
- ID_SITE_CRISP (website_id)

Headers: Content-Type: application/json, X-Crisp-Tier: plugin

Une réponse HTTP 200 ou 206 est considérée valide. 429 est géré comme quota atteint.
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Optional, Dict, Any, Set, List
import requests


# Répertoires et fichiers (possibilité d'overrider dans les tests via monkeypatch)
ROOT_DIR = Path(__file__).resolve().parents[0]
CONV_DIR = ROOT_DIR / "conversations"
CONV_FILE = CONV_DIR / "conversations.jsonl"
USERS_DIR = ROOT_DIR / "utilisateurs"
USERS_FILE = USERS_DIR / "utilisateurs.jsonl"

# Headers requis par l'API
HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}


def extract_email_from_conv(obj: Dict[str, Any]) -> Optional[str]:
    """Extrait l'email depuis un objet conversation. Selon le .jsonl fourni,
    l'email se trouve dans la clé `meta.email` à la racine ou dans `data.meta.email`.
    Retourne None si introuvable.
    """
    if not isinstance(obj, dict):
        return None
    # cas courant: meta.email à la racine
    meta = obj.get("meta")
    if isinstance(meta, dict):
        email = meta.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()

    # autre forme: data -> meta -> email
    data = obj.get("data")
    if isinstance(data, dict):
        meta2 = data.get("meta")
        if isinstance(meta2, dict):
            email = meta2.get("email")
            if isinstance(email, str) and email.strip():
                return email.strip()

    return None


def read_existing_users() -> Dict[str, Dict[str, Any]]:
    """Lit le fichier utilisateurs.jsonl existant et renvoie un mapping email->obj.
    Ignorer les lignes malformées.
    """
    res: Dict[str, Dict[str, Any]] = {}
    if not USERS_FILE.exists():
        return res
    with USERS_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            email = extract_email_from_person(obj)
            if email:
                res[email] = obj
    return res


def extract_email_from_person(obj: Dict[str, Any]) -> Optional[str]:
    """Extrait l'email depuis un objet person retourné par l'API (ou stocké).
    Selon la doc l'email peut être à la racine sous 'email' ou sous 'data'... On gère
    les cas simples.
    """
    if not isinstance(obj, dict):
        return None
    e = obj.get("email")
    if isinstance(e, str) and e.strip():
        return e.strip()
    # parfois sous 'data' -> 'email' ou 'attributes'
    d = obj.get("data")
    if isinstance(d, dict):
        e2 = d.get("email")
        if isinstance(e2, str) and e2.strip():
            return e2.strip()
    # fallback: search any top-level string value containing '@'
    for k, v in obj.items():
        if isinstance(v, str) and "@" in v:
            return v.strip()
    return None


def call_person_api(website_id: str, people_id: str, auth) -> Optional[requests.Response]:
    """Appelle l'API Crisp pour récupérer le profil d'un people_id (ici email encodé dans l'URL).
    Retourne la Response ou None en cas d'exception.
    """
    # people_id doit être encodé pour l'URL
    from urllib.parse import quote

    pid = quote(people_id, safe="")
    url = f"https://api.crisp.chat/v1/website/{website_id}/people/profile/{pid}"
    try:
        resp = requests.get(url, headers=HEADERS, auth=auth, timeout=30)
        return resp
    except requests.RequestException as e:
        print(f"Erreur réseau lors de l'appel API pour {people_id}: {e}")
        return None


def load_emails_from_conversations() -> List[str]:
    """Parcourt le fichier conversations.jsonl et retourne la liste d'emails (avec duplicates).
    On lit ligne à ligne pour limiter la mémoire.
    """
    emails: List[str] = []
    if not CONV_FILE.exists():
        return emails
    with CONV_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            email = extract_email_from_conv(obj)
            if email:
                emails.append(email)
    return emails


def save_users_sorted(users_map: Dict[str, Dict[str, Any]]) -> None:
    """Sauve le mapping email->obj dans USERS_FILE trié par email alphabétique.
    Réécrit entièrement le fichier.
    """
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    emails_sorted = sorted(users_map.keys(), key=lambda s: s.lower())
    with USERS_FILE.open("w", encoding="utf-8") as f:
        for e in emails_sorted:
            obj = users_map[e]
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Exporter les profils utilisateurs depuis Crisp")
    parser.add_argument("--nb", type=int, default=50, help="Nombre max d'utilisateurs à exporter (défaut 50)")
    parser.add_argument("--reset", action="store_true", help="Supprimer le fichier utilisateurs.jsonl avant d'exécuter")
    args = parser.parse_args()

    identifier = os.getenv("CRISP_IDENTIFIER_PROD")
    key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")
    if not identifier or not key or not website_id:
        print("Les variables d'environnement CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définies.")
        sys.exit(1)

    # Prépare dossier
    USERS_DIR.mkdir(parents=True, exist_ok=True)

    if args.reset and USERS_FILE.exists():
        USERS_FILE.unlink()
        print("Fichier utilisateurs supprimé (reset).")

    # Auth HTTP Basic
    auth = (identifier, key)

    # Lire existants
    existing = read_existing_users()
    existing_initial = len(existing)

    # Charger emails depuis conversations
    emails = load_emails_from_conversations()
    if not emails:
        print("Aucun email trouvé dans le fichier de conversations.")
        # afficher récap
        print("--- Récapitulatif ---")
        print(f"Utilisateurs initialement présents: {existing_initial}")
        print(f"Nouveaux utilisateurs ajoutés lors de cette exécution: 0")
        print(f"Utilisateurs totaux dans le fichier: {existing_initial}")
        return

    # Utiliser un set pour conserver l'ordre de traitement unique tout en parcourant
    seen_in_run: Set[str] = set()
    emails_unique_ordered: List[str] = []
    for e in emails:
        if e not in seen_in_run:
            seen_in_run.add(e)
            emails_unique_ordered.append(e)

    target = args.nb
    added = 0
    ignored = 0

    for email in emails_unique_ordered:
        # Si déjà dans existing, on ignore (n'entre pas dans le quota)
        if email in existing:
            ignored += 1
            print(f"Ignoré (déjà présent) : {email}")
            continue

        if added >= target:
            break

        print(f"Traitement: {email}")
        resp = call_person_api(website_id, email, auth)
        if resp is None:
            print(f"Échec de l'appel pour {email}, on passe au suivant.")
            continue

        if resp.status_code == 429:
            print("Réponse 429: quota d'appels atteint. Arrêt des requêtes.")
            break

        if resp.status_code not in (200, 206):
            print(f"Réponse inattendue pour {email}: {resp.status_code} {getattr(resp, 'text', '')}")
            continue

        # tenter de décoder
        try:
            person = resp.json()
        except Exception:
            print(f"Impossible de décoder JSON pour {email}, on passe.")
            continue

        # enregistrer dans existing
        person_email = extract_email_from_person(person) or email
        existing[person_email] = person
        added += 1
        # sauvegarde incrémentale: réécrire le fichier trié après chaque ajout (sécurise contre crash)
        save_users_sorted(existing)

        # petite pause pour respecter quotas
        time.sleep(0.1)

    total_final = len(existing)
    print("--- Récapitulatif ---")
    print(f"Utilisateurs initialement présents: {existing_initial}")
    print(f"Nouveaux utilisateurs ajoutés lors de cette exécution: {added}")
    print(f"Utilisateurs ignorés (déjà présents) lors de cette exécution: {ignored}")
    print(f"Utilisateurs totaux dans le fichier: {total_final}")


if __name__ == "__main__":
    main()
