#!/usr/bin/env python3
"""
users.py

Script pour exporter les profils utilisateurs (people/profile) depuis l'API Crisp
en se basant sur les emails trouvés dans `conversations/conversations.jsonl` (meta.email).

Comportement principal:
- Lit `conversations/conversations.jsonl` et extrait les emails depuis `meta.email`.
- Vérifie `/utilisateurs/utilisateurs.jsonl` pour éviter d'ajouter des doublons.
- Pour chaque email absent, appelle l'API Crisp `people/profile/:people_id` en utilisant
  l'email comme people_id et ajoute la réponse JSON dans `/utilisateurs/utilisateurs.jsonl`.
- Gère les options `--nb` (nombre max à exporter, défaut 50) et `--reset` (supprime le fichier utilisateurs.jsonl avant démarrage).
- Affiche la progression (email traité) et un récapitulatif final.

Variables d'environnement requises:
- CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD, ID_SITE_CRISP

Entêtes utilisés pour Crisp API:
- Content-Type: application/json
- X-Crisp-Tier: plugin

Gestion des codes HTTP:
- 200 et 206 considérés valides.
- 429 gère le dépassement de quota et arrête proprement.

Fichier généré: `utilisateurs/utilisateurs.jsonl`

Commentaires en français.
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Dict, Any, Set, List, Optional, Tuple

import requests

# Constantes et chemins
ROOT = Path(__file__).resolve().parent
# Alias utilisés par les tests et cohérence avec les autres scripts
ROOT_DIR = ROOT
CONV_DIR = ROOT / "conversations"
CONV_FILE = CONV_DIR / "conversations.jsonl"
USERS_DIR = ROOT / "utilisateurs"
USERS_FILE = USERS_DIR / "utilisateurs.jsonl"

HEADERS = {
    "Content-Type": "application/json",
    "X-Crisp-Tier": "plugin",
}


def read_existing_users() -> Dict[str, Dict[str, Any]]:
    """Lit le fichier utilisateurs.jsonl et renvoie un dict email -> objet utilisateur.
    Ignore les lignes malformées.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not USERS_FILE.exists():
        return out
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
                # essayer de récupérer l'email dans l'objet retourné par l'API
                email = extract_email_from_profile(obj)
                if email:
                    out[email.lower()] = obj
    except Exception:
        # en cas d'erreur, retourner dict vide
        return {}
    return out


def extract_email_from_profile(profile: Dict[str, Any]) -> Optional[str]:
    """Extrait l'email depuis le profil retourné par l'API Crisp.
    Selon la doc, l'email peut se trouver à la racine sous 'email' ou dans 'data' ou 'properties'.
    On normalise en minuscule.
    """
    if not isinstance(profile, dict):
        return None
    # clés possibles
    for key in ("email",):
        v = profile.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    # parcourir data
    data = profile.get("data")
    if isinstance(data, dict):
        e = data.get("email") or data.get("emails")
        if isinstance(e, str) and e.strip():
            return e.strip().lower()
    # la structure Crisp peut contenir 'properties' ou 'attributes'
    for key in ("properties", "attributes"):
        p = profile.get(key)
        if isinstance(p, dict):
            for subk in ("email", "emails"):
                ev = p.get(subk)
                if isinstance(ev, str) and ev.strip():
                    return ev.strip().lower()
    return None


def extract_email_from_person(obj: Dict[str, Any]) -> Optional[str]:
    """Alias/simplification pour les tests: extrait l'email depuis un objet 'person'."""
    return extract_email_from_profile(obj)


def extract_emails_from_conversations() -> List[str]:
    """Lit `conversations.jsonl` et retourne la liste d'emails (dédoublonnée) trouvés dans meta.email.
    Conserve l'ordre d'apparition.
    """
    emails: List[str] = []
    seen: Set[str] = set()
    if not CONV_FILE.exists():
        print(f"Fichier de conversations introuvable: {CONV_FILE}")
        return []
    try:
        with CONV_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                # meta.email à la racine ou sous data.meta selon différents exports
                meta = None
                if isinstance(obj, dict):
                    meta = obj.get("meta") if isinstance(obj.get("meta"), dict) else None
                    # certains exports ont data.meta.email
                    if meta is None and isinstance(obj.get("data"), dict):
                        meta = obj.get("data", {}).get("meta") if isinstance(obj.get("data", {}).get("meta"), dict) else None
                if not isinstance(meta, dict):
                    continue
                email = meta.get("email")
                if not email or not isinstance(email, str):
                    continue
                email_norm = email.strip().lower()
                if not email_norm:
                    continue
                if email_norm in seen:
                    continue
                seen.add(email_norm)
                emails.append(email_norm)
    except Exception as e:
        print(f"Erreur lors de la lecture des conversations: {e}")
        return []
    return emails


def call_people_api(website_id: str, people_id: str, auth: Tuple[str, str]) -> Optional[requests.Response]:
    """Appelle l'API Crisp pour récupérer le profil d'un people_id (ici l'email).
    Retourne la Response ou None en cas d'erreur réseau.
    """
    # people_id (email) doit être percent-encodé pour être placé dans l'URL
    try:
        from urllib.parse import quote
        pid = quote(people_id, safe="")
    except Exception:
        pid = people_id
    url = f"https://api.crisp.chat/v1/website/{website_id}/people/profile/{pid}"
    try:
        resp = requests.get(url, headers=HEADERS, auth=auth, timeout=30)
        return resp
    except requests.RequestException as e:
        print(f"Erreur réseau lors de l'appel people API pour {people_id}: {e}")
        return None


def write_users_file(all_users: List[Dict[str, Any]]) -> None:
    """Écrit la liste `all_users` dans USERS_FILE en JSONL (écrase). Tri par email alphabétique."""
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    # Trier par email
    def key_email(u: Dict[str, Any]) -> str:
        e = extract_email_from_profile(u)
        return e or ""

    sorted_users = sorted(all_users, key=lambda u: key_email(u) or "")
    with USERS_FILE.open("w", encoding="utf-8") as f:
        for u in sorted_users:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")


def append_user_to_file(user_obj: Dict[str, Any]) -> None:
    """Ajoute un utilisateur à la fin du fichier USERS_FILE (append)."""
    USERS_DIR.mkdir(parents=True, exist_ok=True)
    with USERS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(user_obj, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Exporter les profils utilisateurs via l'API Crisp")
    parser.add_argument("--nb", type=int, default=50, help="Nombre max d'utilisateurs à exporter (défaut 50)")
    parser.add_argument("--reset", action="store_true", help="Supprimer le fichier utilisateurs.jsonl avant traitement")
    args = parser.parse_args()

    # Vérifier variables d'environnement
    identifier = os.getenv("CRISP_IDENTIFIER_PROD")
    key = os.getenv("CRISP_KEY_PROD")
    website_id = os.getenv("ID_SITE_CRISP")
    if not identifier or not key or not website_id:
        print("Les variables d'environnement CRISP_IDENTIFIER_PROD, CRISP_KEY_PROD et ID_SITE_CRISP doivent être définies.")
        sys.exit(1)

    auth = (identifier, key)

    # Préparer dossier
    USERS_DIR.mkdir(parents=True, exist_ok=True)

    if args.reset and USERS_FILE.exists():
        USERS_FILE.unlink()
        print("Fichier utilisateurs supprimé (reset).")

    # Charger utilisateurs existants
    existing = read_existing_users()
    existing_count_initial = len(existing)

    # Récupérer emails depuis conversations
    emails = extract_emails_from_conversations()
    if not emails:
        print("Aucun email trouvé dans les conversations. Rien à faire.")
        print(f"Utilisateurs totaux dans le fichier: {len(existing)}")
        return

    processed = 0
    skipped = 0
    added = 0

    # Parcours des emails et appel API pour ceux qui ne sont pas présents
    for email in emails:
        if processed >= args.nb:
            break
        # Affichage de la progression lisible
        print(f"Traitement email: {email} ...")

        if email in existing:
            skipped += 1
            processed += 1
            print(f"  -> déjà présent, ignoré.")
            continue

        # Appeler l'API avec l'email comme people_id
        resp = call_people_api(website_id, email, auth)
        if resp is None:
            print(f"  -> échec réseau pour {email}, on passe.")
            processed += 1
            continue

        if resp.status_code == 429:
            print("429 reçu: quota d'appels atteint. Arrêt du traitement.")
            break

        if resp.status_code not in (200, 206):
            print(f"  -> réponse inattendue {resp.status_code} pour {email}. Ignoré.")
            processed += 1
            continue

        try:
            profile = resp.json()
        except Exception:
            print(f"  -> impossible de décoder la réponse JSON pour {email}. Ignoré.")
            processed += 1
            continue

        # Extraire un email dans la réponse pour indexation. Si absent, on conserve sous la clé fournie
        prof_email = extract_email_from_profile(profile) or email

        # Ajouter au fichier (append) et à la mémoire
        append_user_to_file(profile)
        existing[prof_email] = profile
        added += 1
        processed += 1
        # petite pause pour respecter quota
        time.sleep(0.1)

    # Après le traitement, réécrire le fichier trié par email pour garantir l'ordre et l'unicité
    all_users = list(existing.values())
    # Dédupliquer par email (au cas où plusieurs profils sans email ou avec sinon)
    dedup_index: Dict[str, Dict[str, Any]] = {}
    for u in all_users:
        e = extract_email_from_profile(u)
        if e:
            dedup_index[e] = u
        else:
            # stocker avec clé spéciale en ajoutant un suffixe pour conserver
            key = json.dumps(u, sort_keys=True)
            dedup_index[key] = u

    final_users = list(dedup_index.values())
    write_users_file(final_users)

    # Récapitulatif final
    total_final = len(final_users)
    print("--- Récapitulatif ---")
    print(f"Utilisateurs initialement présents: {existing_count_initial}")
    print(f"Utilisateurs ajoutés lors de cette exécution: {added}")
    print(f"Utilisateurs ignorés (déjà présents) pendant l'exécution: {skipped}")
    print(f"Utilisateurs totaux dans le fichier: {total_final}")


if __name__ == "__main__":
    main()
