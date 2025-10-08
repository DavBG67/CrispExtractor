import sys
from pathlib import Path

# Ajouter la racine du projet au path pour que pytest puisse importer conv.py
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
