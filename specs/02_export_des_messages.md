## 🎯 Objectif

- Ce script doit permettre d'exporter tous les messages des conversations exportées par le script conv.py en utilisant l'API Crisp.

- Il doit créer pour chaque conversation un fichier .jsonl contenant tous les messages de la conversation. Chaque fichier sera nommé d'après l'identifiant "session_id" de la conversation et sera placé dans le répertoire /conversations/messages/.

---

## 🧩 Contexte

- J'ai besoin d'analyser les conversations de mon compte Crisp mais Crisp ne permet pas de faire un export direct. En revanche une API est disponible. Je veux donc une série de scripts avec chacun une tâche précise pour récupérer les conversations Crisp via l'API.

- Cette spécification concerne le script mess.py dont le rôle sera d'exporter individuellement dans des fichiers .jsonl toutes les conversations de mon compte préalablement listées dans le fichier /conversations/conversations.jsonl par le script conv.py.

- Ces fichiers .jsonl seront ensuite utilisés par d'autres scripts.

---

## ✅ Critères d’acceptation

- Les fichiers générés seront nommés d'après l'identifiant "session_id" de la conversation et seront placés dans le répertoire /conversations/messages/.

- Ces fichiers contiendront l'ensemble des informations retournées par l'API Crisp : https://docs.crisp.chat/references/rest-api/v1/#get-messages-in-conversation.

- L'identifiant unique des messages est le champ "fingerprint". Il ne devra pas y avoir de doublons de messages dans les fichiers .jsonl de conversations.

- Régulièrement au cours de l'exécution, le script fera des affichages (print) lisibles indiquant son avancée : l'id de la conversation traitée, le nombre de messages exportés, le nombre de messages ignorés.

- En fin d'exécution, un affichage récapitulatif sera effectué qui indiquera combien de conversations ont été traitées et ignorées au cours de l'exécution et combien de fichiers .jsonl de conversations sont désormais présents dans le répertoire.

- A la fin de l'exécution du script, chaque fichier .jsonl de conversation contiendra les messages de la conversation triés du plus récent au plus ancien.

---

## 🧠 Contraintes techniques

- Le script se nommera mess.py et sera écrit en Python.

- Le script sera aussi propre et lisible que possible et contiendra des commentaires en français.

- La documentation du repository et le README.md seront mis à jour si besoin à chaque nouveau traitement de cette spécification.

- Le script utilisera l'API : https://api.crisp.chat/v1/website/:website_id/conversation/:session_id/messages/.

- L'API sera appelée avec les headers : "Content-Type": "application/json" et "X-Crisp-Tier": "plugin".

- Les paramètres d'authentification pour l'API sont enregistrées dans les variables d'environnement (identifiant et clés se trouvent respectivement dans les variables CRISP_IDENTIFIER_PROD et CRISP_KEY_PROD)

- L'id du site pour les appels API se trouve dans une variable d'environnement : ID_SITE_CRISP.

- Une réponse HTTP 206 de l'API (partial content) sera considérée comme valide au même titre qu'une réponse HTTP 200.

- Une réponse HTTP 429 signifie que le quota d'appels à l'API est atteint, il faut la gérer proprement.

- Le script prendra un paramètre optionnel --nb suivi d'un nombre qui correspondra au nombre maximum de conversations pour lesquelles on procèdera à des appels API lors de son exécution (ex: mess.py --nb 100). Si le paramètre n'est pas fourni le script traitera un nombre par défaut de 50 conversations.

- Le script passera en revue le fichier /conversations/conversations.jsonl et pour chaque conversation trouvée commencera par vérifier si le fichier des messages de cette conversation existe dans le répertoire /conversations/messages/. Si le fichier n'existe pas, le script utilisera l'API Crisp pour exporter les messages de la conversation et les stocker dans un nouveau fichier. Si le fichier existe, le script devra le mettre à jour uniquement si l'API Crisp retourne de nouveau messages. L'objectif est d'appeler l'API de manière à avoir en premier les messages les plus récents. S'il existe des messages qui n'étaient pas présents dans le fichier .jsonl, il faut les ajouter. Quand l'API ne retourne plus que des messages déjà présents dans le fichier, il n'est plus utile de faire de nouveaux appels pour cette conversation. L'identifiant unique des messages est le champ "fingerprint".

- La pagination sera gérée avec le paramètre "timestamp_before" dans l'appel API comme indiqué ici : https://docs.crisp.chat/references/rest-api/v1/#get-messages-in-conversation. A chaque appel de l'API on analysera les messages exportés et en particulier la valeur du champ "timestamp" pour stocker le timestamp le plus ancien et s'en servir pour l'appel API paginé suivant.

- Un fichier d'état et reprise (/conversations/messages/messages.jsonl.state.json) sera utilisé pour garder en mémoire la pagination (le prochain session_id à utiliser) et pouvoir reprendre l'exportation là où elle s'était arrêtée au cours du précédent appel du script.

- Un paramètre optionnel --reset aura pour conséquence de réinitialiser le fichier d'état et d'exécuter le script comme si c'était la première fois, en recommençant par la première conversation du fichier /conversations/conversations.jsonl.

---

## 🔍 Tests attendus

- Je te laisse carte blanche pour les tests.

---

## 🔄 Évolutivité


---

## 📎 Liens et références

- Documentation API Crisp : https://docs.crisp.chat/references/rest-api/v1/
- Format retourné par l'API : https://docs.crisp.chat/references/rest-api/v1/#get-messages-in-conversation
- Lié à #1 
- Lié à #5 
