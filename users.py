#!/usr/bin/env python3
"""
users.py

Script pour exporter les profils utilisateurs (people) depuis l'API Crisp
en se basant sur les adresses emails trouvées dans `/conversations/conversations.jsonl`.

Produits:
- /utilisateurs/utilisateurs.jsonl : fichier JSONL contenant un objet JSON par utilisateur

Comportement principal:
- Parcourt les conversations et récupère l'email dans data>meta>email
- Pour chaque email non présent dans `/utilisateurs/utilisateurs.jsonl`, appelle
  l'API Crisp: GET /v1/website/:website_id/people/profile/:people_id
- Gère 200 et 206 comme succès, 429 arrête l'exécution proprement.
- Évite les doublons, tri final par email (ordre alphabétique)

Options:
- --nb N : nombre max d'utilisateurs à exporter (défaut 50)
- --reset : supprimer `/utilisateurs/utilisateurs.jsonl` avant de démarrer

Variables d'environnement requises:
- CRISP_IDENTIFIER_PROD
- CRISP_KEY_PROD
- ID_SITE_CRISP

"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from urllib.parse import quote

import requests

# Constantes
ROOT_DIR = Path(__file__).parent
CONV_DIR = ROOT_DIR / "conversations"
CONV_FILE = CONV_DIR / "conversations.jsonl"
USERS_DIR = ROOT_DIR / "utilisateurs"
USERS_FILE = USERS_DIR / "utilisateurs.jsonl"

HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}


def load_existing_users() -> Dict[str, Dict[str, Any]]:
    """Lit le fichier utilisateurs.jsonl et renvoie un dict email -> objet user.

    Les entrées sans email sont ignorées.
    """
    res: Dict[str, Dict[str, Any]] = {}
    if not USERS_FILE.exists():
        return res
    try:
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
    except Exception:
        # si lecture impossible, on retourne un dict vide
        return {}
    return res


def save_all_users(users_map: Dict[str, Dict[str, Any]]) -> None:
    """Écrit l'ensemble des utilisateurs triés par email dans USERS_FILE."""
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    sorted_items = sorted(users_map.items(), key=lambda kv: kv[0].lower())
    with USERS_FILE.open("w", encoding="utf-8") as f:
        for _, obj in sorted_items:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def extract_email_from_person(obj: Dict[str, Any]) -> Optional[str]:
    """Tente d'extraire un email depuis l'objet retourné par l'API Crisp.

    L'API peut renvoyer différentes structures ; on tente plusieurs clés.
    """
    if not isinstance(obj, dict):
        return None
    # cas simple
    v = obj.get("email")
    if isinstance(v, str) and v:
        return v
    # parfois sous 'data'
    data = obj.get("data")
    if isinstance(data, dict):
        v2 = data.get("email")
        if isinstance(v2, str) and v2:
            return v2
    # autre endroits possibles
    for key in ("person", "people", "profile"):
        sub = obj.get(key)
        if isinstance(sub, dict):
            e = sub.get("email")
            if isinstance(e, str) and e:
                return e
    return None


def extract_email_from_conversation(conv_obj: Dict[str, Any]) -> Optional[str]:
    """Extrait data>meta>email d'un objet conversation selon la spec."""
    if not isinstance(conv_obj, dict):
        return None
    data = conv_obj.get("data")
    if not isinstance(data, dict):
        return None
    meta = data.get("meta")
    if not isinstance(meta, dict):
        return None
    email = meta.get("email")
    if isinstance(email, str) and email:
        return email
    return None


def call_people_api(website_id: str, people_id: str, auth: Tuple[str, str]) -> Optional[requests.Response]:
    """Appelle l'API Crisp pour récupérer le profil d'une personne (people_id = email).

    people_id est encodé dans l'URL.
    """
    safe_id = quote(people_id, safe="")
    url = f"https://api.crisp.chat/v1/website/{website_id}/people/profile/{safe_id}"
    try:
        resp = requests.get(url, headers=HEADERS, auth=auth, timeout=30)
        return resp
    except requests.RequestException as e:
        print(f"Erreur réseau lors de la récupération du profil {people_id}: {e}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporter les utilisateurs Crisp en JSONL")
    parser.add_argument("--nb", type=int, default=50, help="Nombre max d'utilisateurs à exporter (défaut 50)")
    parser.add_argument("--reset", action="store_true", help="Supprimer le fichier utilisateurs.jsonl avant de démarrer")
    args = parser.parse_args()

    identifier = os.getenv("CRISP_IDENTIFIER_PROD")
    key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")
    if not identifier or not key or not website_id:
        print("Les variables d'environnement CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définies.")
        sys.exit(1)

    # Prépare le dossier utilisateurs
    USERS_DIR.mkdir(parents=True, exist_ok=True)

    if args.reset:
        if USERS_FILE.exists():
            try:
                USERS_FILE.unlink()
                print(f"Fichier {USERS_FILE} supprimé (--reset).")
            except Exception:
                pass

    existing = load_existing_users()
    existing_count_initial = len(existing)

    # Lire les conversations et récupérer la liste d'emails uniques
    emails_seen = []
    emails_unique = []
    if not CONV_FILE.exists():
        print(f"Fichier de conversations introuvable: {CONV_FILE}")
        sys.exit(1)

    with CONV_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            email = extract_email_from_conversation(obj)
            if not email:
                continue
            emails_seen.append(email)
            if email not in emails_unique:
                emails_unique.append(email)

    # Parcours des emails et export via API si nécessaire
    auth = (identifier, key)
    processed = 0
    ignored = 0
    api_fetched = 0

    target = max(0, int(args.nb))

    for email in emails_unique:
        if processed >= target:
            break
        print(f"Traitement utilisateur: {email}")
        if email in existing:
            ignored += 1
            processed += 1
            continue

        resp = call_people_api(website_id, email, auth)
        if resp is None:
            print(f"Échec appel API pour {email}, passage au suivant.")
            processed += 1
            continue

        if resp.status_code == 429:
            print("Réponse 429: quota d'appels atteint. Arrêt prématuré.")
            break

        if resp.status_code not in (200, 206):
            print(f"Réponse inattendue pour {email}: {resp.status_code} {getattr(resp, 'text', '')}")
            processed += 1
            continue

        try:
            person = resp.json()
        except Exception:
            print(f"Impossible de décoder la réponse JSON pour {email}")
            processed += 1
            continue

        # Extraire l'email depuis la réponse (pour clé de tri / dedup)
        resolved_email = extract_email_from_person(person) or email
        # On sauvegarde l'objet tel quel
        existing[resolved_email] = person
        api_fetched += 1
        processed += 1

        # Sauver immédiatement pour persistance et réordonner le fichier
        try:
            save_all_users(existing)
        except Exception:
            print(f"Erreur lors de la sauvegarde pour {email}")

        # Pause courte pour respecter quotas
        time.sleep(0.15)

    final_total = len(existing)
    print("--- Récapitulatif ---")
    print(f"Utilisateurs initialement présents: {existing_count_initial}")
    print(f"Utilisateurs importés depuis l'API lors de cette exécution: {api_fetched}")
    print(f"Utilisateurs ignorés (déjà présents) lors de cette exécution: {ignored}")
    print(f"Utilisateurs totaux dans le fichier: {final_total}")


if __name__ == "__main__":
    main()
