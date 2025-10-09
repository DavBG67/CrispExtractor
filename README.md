# CrispExtractor
Script pour exporter les conversations d'un compte Crisp (site) vers un fichier jsonl.

Usage rapide:

1. Installer les dépendances:

```bash
pip install -r requirements.txt
```

2. Exporter (exemple):

```bash
export CRISP_IDENTIFIER_PROD=... \
	CRISP_KEY_PROD=... \
	ID_SITE_CRISP=...
python conv.py --nb 50
```

Fichiers:

- `conv.py` : script principal.
- `conversations/conversations.jsonl` : fichier créé/complété par le script.
- `conversations/conversations.jsonl.state.json` : fichier d'état utilisé pour reprendre la pagination.

Options utiles:

```bash
python conv.py --nb 50       # exporter jusqu'à 50 nouvelles conversations
python conv.py --reset       # réinitialiser le fichier et l'état
```

Export des messages par conversation
-----------------------------------

Le script `mess.py` permet d'exporter les messages pour chaque conversation listée dans `conversations/conversations.jsonl`.

Exemple d'utilisation:

```bash
export CRISP_IDENTIFIER_PROD=... \
	CRISP_KEY_PROD=... \
	ID_SITE_CRISP=...
python mess.py --nb 50
```

Options:
- `--nb N` : nombre maximum de conversations à traiter (défaut 50)
- `--reset` : réinitialise le fichier d'état `conversations/messages/messages.jsonl.state.json`

