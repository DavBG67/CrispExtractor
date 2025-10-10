## 🎯 Objectif

- Ce script devrapermettre d'exporter toutes les conversations d'un compte Crisp, pour un site donné, en utilisant l'API Crisp. 

- Il devra créer (s'il n'existe pas encore) ou enrichir (s'il existe) un fichier .jsonl qui contiendra toutes les conversations exportées.

---

## 🧩 Contexte

- J'ai besoin d'analyser les conversations de mon compte Crisp mais Crisp ne permet pas de faire un export direct. En revanche une API est disponible. Je veux donc une série de scripts avec chacun une tâche précise pour récupérer les conversations Crisp via l'API.

- Cette spécification concerne le script conv.py dont le rôle sera d'exporter dans un fichier .jsonl toutes les conversations de mon compte, en plusieurs itérations au besoin.

- Ce fichier .jsonl sera ensuite utilisé par d'autres scripts.

---

## ✅ Critères d’acceptation

- Le fichier généré se nommera /conversations/conversations.jsonl.

- Ce fichier contiendra l'ensemble des informations retournées par l'API Crisp : https://docs.crisp.chat/references/rest-api/v1/#website-conversations.

- Régulièrement au cours de l'exécution, le script fera des affichages (print) lisibles indiquant son avancée : le nombre de conversations exportées, le nombre de conversations ignorées parce que déjà présentes dans le fichier.

- En fin d'exécution, un affichage récapitulatif sera effectué qui indiquera combien de conversations ont été exportées et ignorées au cours de l'exécution et combien de conversations le fichier .jsonl comporte désormais.
 
- A la fin de l'exécution du script, les conversations doivent être triées dans le fichier selon la valeur du timestamp correspondant à la date de dernière activité dans la conversation (dans le .json, cela correspond au champ "last" de la balise "active") de manière descendante (conversation modifiée le plus récemment en haut).

- Le fichier .jsonl ne contiendra pas de conversations en doublon. Il doit y avoir une unicité des identifiants "session_id".

---

## 🧠 Contraintes techniques

- Le script se nommera conv.py et sera écrit en Python.

- Le script sera aussi propre et lisible que possible et contiendra des commentaires en français.
 
- La documentation du repository et le README.md seront mis à jour si besoin à chaque nouveau traitement de cette spécification.

- Le script utilisera l'API : https://api.crisp.chat/v1/website/:website_id/conversations/.

- L'API sera appelée avec les headers : "Content-Type": "application/json" et "X-Crisp-Tier": "plugin".

- Les paramètres d'authentification pour l'API sont enregistrées dans les variables d'environnement (identifiant et clés se trouvent respectivement dans les variables CRISP_IDENTIFIER_PROD et CRISP_KEY_PROD)

- L'id du site pour les appels API se trouve dans une variable d'environnement : ID_SITE_CRISP.

- Une réponse HTTP 206 de l'API (partial content) sera considérée comme valide au même titre qu'une réponse HTTP 200.

- Une réponse HTTP 429 signifie que le quota d'appels à l'API est atteint, il faudra la gérer proprement.

- Le script prendra un paramètre optionnel --nb suivi d'un nombre qui correspondra au nombre maximum de nouvelles conversations à exporter lors de son exécution (ex: conv.py --nb 100). Si le paramètre n'est pas fourni le script exportera un nombre par défaut de 400 conversations. 

- Le script bouclera tant que le nombre de conversations demandé via le paramètre (ou le nombre par défaut) ne sera pas atteint ou bien tant que le quota d'appel API ne sera pas atteint ou bien tant que la réponse de l'API contiendra des résultats.

- Les nouvelles conversations seront ajoutées au fichier à chaque tour dans la boucle d'appel API, à condition que la réponse de l'API soit positive et que de nouvelles conversations aient pu être exportées.

- La pagination sera gérée avec le paramètre "page_number" dans l'appel API et un paramètre "per_page" fixé à 20 : https://api.crisp.chat/v1/website/:website_id/conversations/:page_number?per_page=20.

- Un fichier d'état et reprise (/conversations/conversations.jsonl.state.json) sera utilisé pour garder en mémoire la pagination (le prochain page_number à utiliser) et pouvoir reprendre l'exportation là où elle s'était arrêtée au cours du précédent appel du script.

- A chaque lancement, le script vérifiera si le fichier /conversations/conversations.jsonl existe. S'il n'existe pas il sera créé et les conversations récupérées au cours de l'exécution seront stockées dedans. S'il existe, les conversations récupérées au cours de l'exécution seront stockées dedans à condition qu'elles ne s'y trouvent pas déjà (il faut une unicité des conversations dans le fichier). 

- Le script doit identifier les conversations via l'identifiant session_id qui se trouve dans la liste data (https://docs.crisp.chat/references/rest-api/v1/#website-conversations). Les conversations sans identifiant détectable seront ignorées et comptées dans les statistiques « ignorées » pour éviter les doublons dans le fichier.

- Un paramètre optionnel --reset aura pour conséquence de commencer par effacer le fichier /conversations/conversations.jsonl et de réinitialiser le fichier d'état. Le script doit alors se comporter comme s'il était lancé pour la première fois.

---

## 🔍 Tests attendus

- Je te laisse carte blanche pour les tests.

---

## 🔄 Évolutivité

---

## 📎 Liens et références 

- Documentation API Crisp : https://docs.crisp.chat/references/rest-api/v1/
- Format retourné par l'API : https://docs.crisp.chat/references/rest-api/v1/#website-conversations
- Lié à #4 
- Lié à #5 
