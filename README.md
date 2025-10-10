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

Tests
-----

Les tests unitaires utilisent pytest. Pour les lancer:

    pytest -q

Licence
-------

MIT
