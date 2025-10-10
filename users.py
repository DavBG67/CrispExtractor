#!/usr/bin/env python3
"""
users.py

Script pour exporter les profils utilisateurs via l'API Crisp.

Comportement :
- Parcourt `conversations/conversations.jsonl` et extrait les emails (data>meta>email).
- Pour chaque email non présent dans `/utilisateurs/utilisateurs.jsonl`, appelle l'API
  `https://api.crisp.chat/v1/website/:website_id/people/profile/:people_id` et sauvegarde
  la réponse JSON dans `/utilisateurs/utilisateurs.jsonl` (un JSON par ligne).
- Gère les options `--nb` (défaut 50) et `--reset` (supprime le fichier utilisateurs avant d'exécuter).

Le script affiche régulièrement la progression (email en cours) et un récapitulatif final.

Variables d'environnement requises :
- CRISP_IDENTIFIER_PROD
- CRISP_KEY_PROD
- ID_SITE_CRISP

"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import quote

import requests

# Répertoires et fichiers par défaut (peuvent être patchés dans les tests)
ROOT_DIR = Path(__file__).parent
CONV_DIR = ROOT_DIR / "conversations"
CONV_FILE = CONV_DIR / "conversations.jsonl"
USERS_DIR = ROOT_DIR / "utilisateurs"
USERS_FILE = USERS_DIR / "utilisateurs.jsonl"

# En-têtes requis pour l'API Crisp
HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}


def extract_email_from_conversation(conv_obj: Dict[str, Any]) -> Optional[str]:
    """Extrait l'email depuis un objet conversation (data>meta>email).
    Retourne None si non trouvé.
    """
    try:
        if not isinstance(conv_obj, dict):
            return None
        data = conv_obj.get("data")
        if not isinstance(data, dict):
            return None
        meta = data.get("meta")
        if not isinstance(meta, dict):
            return None
        email = meta.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip()
    except Exception:
        return None
    return None


def extract_email_from_person(person_obj: Dict[str, Any]) -> Optional[str]:
    """Extrait un email depuis un objet 'person' retourné par l'API Crisp.
    Les réponses peuvent varier, on cherche d'abord la clé 'email'.
    """
    if not isinstance(person_obj, dict):
        return None
    # Forme la plus directe
    e = person_obj.get("email")
    if isinstance(e, str) and e.strip():
        return e.strip()
    # Parfois l'objet peut être emboîté
    data = person_obj.get("data")
    if isinstance(data, dict):
        e = data.get("email")
        if isinstance(e, str) and e.strip():
            return e.strip()
    return None


def read_existing_users() -> Dict[str, Dict[str, Any]]:
    """Lit le fichier utilisateurs.jsonl (s'il existe) et renvoie un dict email->person_obj."""
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
                    email = extract_email_from_person(obj)
                    if email:
                        res[email] = obj
                except Exception:
                    # ignorer lignes malformées
                    continue
    except Exception:
        # si lecture impossible, retourner vide
        return {}
    return res


def call_api_get_person(website_id: str, people_id: str, auth) -> Optional[requests.Response]:
    """Appelle l'API Crisp pour récupérer le profil d'un utilisateur identifé par people_id.
    people_id doit être déjà encodé pour l'URL.
    Retourne l'objet Response ou None en cas d'erreur réseau.
    """
    url = f"https://api.crisp.chat/v1/website/{website_id}/people/profile/{people_id}"
    try:
        resp = requests.get(url, headers=HEADERS, auth=auth, timeout=30)
        return resp
    except requests.RequestException as e:
        print(f"Erreur réseau lors de l'appel API pour {people_id}: {e}")
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporter les utilisateurs (profiles) depuis Crisp en JSONL")
    parser.add_argument("--nb", type=int, default=50, help="Nombre max d'utilisateurs à exporter (défaut 50)")
    parser.add_argument("--reset", action="store_true", help="Supprimer le fichier utilisateurs.jsonl avant d'exécuter")
    args = parser.parse_args()

    # Vérifier variables d'environnement
    identifier = os.getenv("CRISP_IDENTIFIER_PROD")
    key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")
    if not identifier or not key or not website_id:
        print("Les variables d'environnement CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définies.")
        sys.exit(1)

    # Préparer le dossier utilisateurs
    USERS_DIR.mkdir(parents=True, exist_ok=True)

    # Reset si demandé
    if args.reset:
        if USERS_FILE.exists():
            USERS_FILE.unlink()
        print("Fichier utilisateurs supprimé (reset).")

    # Auth HTTP Basic (identifier:key)
    auth = (identifier, key)

    # Charger les utilisateurs existants
    existing = read_existing_users()
    initial_count = len(existing)

    # Lire les conversations et extraire emails
    if not CONV_FILE.exists():
        print(f"Fichier de conversations introuvable: {CONV_FILE}")
        sys.exit(1)

    processed_new = 0
    ignored = 0
    to_write = []
    seen_in_run = set()

    target_nb = args.nb if args.nb and args.nb > 0 else 50

    try:
        with CONV_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                if processed_new >= target_nb:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    ignored += 1
                    continue
                email = extract_email_from_conversation(obj)
                if not email:
                    ignored += 1
                    continue
                # Eviter dupes dans la même exécution
                if email in seen_in_run:
                    ignored += 1
                    continue
                seen_in_run.add(email)

                # Si déjà présent dans le fichier, ignorer
                if email in existing:
                    print(f"Déjà présent, ignoré: {email}")
                    ignored += 1
                    continue

                # Affichage de progression
                print(f"Traitement: {email}")

                # Appel API
                enc = quote(email, safe="")
                resp = call_api_get_person(website_id, enc, auth)
                if resp is None:
                    print(f"Échec appel API pour {email}, arrêt.")
                    break

                if resp.status_code == 429:
                    print("Réponse 429: quota d'appels atteint. Arrêt prématuré.")
                    break

                if resp.status_code not in (200, 206):
                    print(f"Réponse inattendue pour {email}: {resp.status_code} {getattr(resp, 'text', '')}")
                    ignored += 1
                    continue

                try:
                    person = resp.json()
                except Exception:
                    print(f"Impossible de décoder JSON pour {email}, ignoré.")
                    ignored += 1
                    continue

                # Récupérer email depuis l'objet retourné (pour s'assurer qu'on a bien l'email)
                person_email = extract_email_from_person(person)
                if not person_email:
                    # Si l'API ne renvoie pas d'email, on ignore
                    print(f"Aucun email trouvé dans la réponse API pour {email}, ignoré.")
                    ignored += 1
                    continue

                # Sauvegarder en mémoire pour écriture
                existing[person_email] = person
                to_write.append(person)
                processed_new += 1

                # Petite pause pour respecter quota
                time.sleep(0.05)

    except KeyboardInterrupt:
        print("Interrompu par l'utilisateur")

    # Si on a ajouté des utilisateurs, écrire dans le fichier
    if to_write:
        # Ecrire d'abord en append (optionnel), puis réécrire trié pour maintenir ordre alphabétique
        with USERS_FILE.open("w", encoding="utf-8") as f:
            # Trier les utilisateurs par email alphabétiquement
            all_users = list(existing.items())
            all_users_sorted = sorted(all_users, key=lambda it: (it[0] or ""))
            for _, person in all_users_sorted:
                f.write(json.dumps(person, ensure_ascii=False) + "\n")

    final_total = len(existing)

    # Récapitulatif
    print("--- Récapitulatif ---")
    print(f"Utilisateurs initialement présents: {initial_count}")
    print(f"Nouveaux utilisateurs exportés lors de cette exécution: {processed_new}")
    print(f"Utilisateurs ignorés lors de cette exécution: {ignored}")
    print(f"Utilisateurs totaux dans le fichier: {final_total}")


if __name__ == "__main__":
    main()
