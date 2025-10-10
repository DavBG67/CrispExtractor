CrispExtractor
===============

Script pour exporter les conversations d'un compte Crisp via l'API.

Usage
-----

Variables d'environnement requises:
- CRISP_IDENTIFIER_PROD
- CRISP_KEY_PROD
- ID_SITE_CRISP

Exemple d'exécution:

    python3 conv.py --nb 200

Exporter les utilisateurs (profiles)
----------------------------------

Le script `users.py` parcourt le fichier `/conversations/conversations.jsonl`, extrait
les adresses email (data>meta>email) et appelle l'API Crisp pour récupérer les profils
assocíés. Les profils sont sauvegardés dans `/utilisateurs/utilisateurs.jsonl`.

Usage:

    python3 users.py --nb 100

Options:
- `--nb N` : nombre maximum d'utilisateurs à exporter (défaut 50)
- `--reset` : supprimer `/utilisateurs/utilisateurs.jsonl` avant d'exécuter


Options:
- --nb N : nombre maximal de nouvelles conversations à exporter (défaut 400)
- --reset : supprimer le fichier de conversations et l'état avant de démarrer

Fichiers produits:
- /conversations/conversations.jsonl : fichier JSONL contenant les conversations exportées
- /conversations/conversations.jsonl.state.json : fichier d'état pour la pagination

Export des messages par conversation
----------------------------------

Le script `mess.py` permet d'exporter les messages pour chaque conversation listée
dans `/conversations/conversations.jsonl`. Il produit un fichier par conversation:

- `/conversations/messages/{session_id}.jsonl`

Usage minimal:

    python3 mess.py --nb 50

Options:
- `--nb N` : nombre max de conversations à traiter (défaut 50)
- `--reset` : réinitialiser le fichier d'état `/conversations/messages/messages.jsonl.state.json`

Le script utilise les variables d'environnement listées ci-dessus pour l'authentification.

Détails pour `mess.py`
----------------------

Variables d'environnement requises:

- `CRISP_IDENTIFIER_PROD` : identifiant API (login)
- `CRISP_KEY_PROD` : clé API (password)
- `ID_SITE_CRISP` : identifiant du site (website_id)

Comportement important:

- Les fichiers de conversations doivent être présents dans `/conversations/conversations.jsonl`.
- Pour chaque conversation, `mess.py` crée/met à jour `/conversations/messages/{session_id}.jsonl`.
- Le script conserve un fichier d'état `/conversations/messages/messages.jsonl.state.json` pour
    reprendre le traitement (option `--reset` pour repartir de zéro).
- Les appels API utilisent les headers `Content-Type: application/json` et `X-Crisp-Tier: plugin`.
- Le script accepte `--nb N` pour limiter le nombre de conversations traitées (défaut 50).

Exemple:

        # traiter 200 conversations et repartir de l'index sauvegardé
        python3 mess.py --nb 200

        # réinitialiser l'état et traiter les 100 premières conversations
        python3 mess.py --nb 100 --reset

Tests
-----

Les tests unitaires utilisent pytest. Pour les lancer:

    pytest -q

Licence
-------

MIT
