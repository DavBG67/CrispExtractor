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

Tests
-----

Les tests unitaires utilisent pytest. Pour les lancer:

    pytest -q

Licence
-------

MIT
