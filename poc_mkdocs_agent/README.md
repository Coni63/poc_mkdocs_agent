# poc_mkdocs_agent

Mini webapp FastAPI :

- **Homepage** : liste des projets déclarés dans `data.json`.
- **Sélection d'un projet** : le site MkDocs est buildé dans un dossier temporaire (supprimé à l'arrêt de l'app), puis affiché.
- **Page de rendu** : le site est affiché en plein écran, avec un widget de chat en bas à droite.

## Prérequis

- Python 3.10+
- [Poetry](https://python-poetry.org/)

## Installation

```bash
cd poc_mkdocs_agent
poetry install
```

## Lancer l'app

```bash
poetry run uvicorn poc_mkdocs_agent.main:app --reload
```

Puis ouvrir <http://127.0.0.1:8000>.

Sans Poetry, avec le venv existant (Windows) :

```powershell
.venv\Scripts\python.exe -m uvicorn poc_mkdocs_agent.main:app --reload
```

## Clé API (chat)

Le chat utilise DeepSeek (API compatible OpenAI). Renseigner la clé dans `.env` :

```
AGENT_KEY=sk-...
```

## Chat

Widget en bas à droite de la page de rendu. Commandes :

- `/question <question>` : répond à partir de la doc du projet. Tous les `.md` (`<projet>/docs`) sont envoyés
  au LLM dans un préfixe de prompt stable (fichiers triés, historique ajouté à la fin) pour profiter du cache
  de préfixe. L'historique est conservé par session et par projet (10 derniers tours) et remis à zéro si la doc change.
- `/update <modification>` : demande au LLM des edits structurés (`path`, `old_str`, `new_str`), les valide
  (fichier `.md` sous `docs/`, `old_str` présent exactement une fois, 1 retry avec les erreurs si invalide),
  puis les commite sur une nouvelle branche `agent/update-<slug>-<timestamp>`. Le commit se fait dans un
  `git worktree` temporaire : le checkout du projet n'est jamais modifié. Le chat affiche le résumé, la branche
  et le diff. Pas de push ni de PR pour l'instant. Le projet doit être un dépôt git.
- Toute autre entrée : message d'erreur dans le chat.

## Configuration des projets

`data.json` associe un nom de projet à son dossier (chemin relatif à `data.json` ou absolu) :

```json
{
    "project_demo": "../poc_mkdocs_doc"
}
```

Si le projet contient un `mkdocs.yml`, il est utilisé tel quel. Sinon, une config minimale est générée
(dans le dossier temporaire) avec `docs_dir` = `<projet>/docs`. Le projet n'est jamais modifié.

## Structure

```
data.json                          # projets disponibles
poc_mkdocs_agent/main.py           # app FastAPI (home, build, rendu, endpoint /api/chat)
poc_mkdocs_agent/chat.py           # commandes, chargement de la doc, historique de session
poc_mkdocs_agent/llm.py            # interface LLM + implémentation DeepSeek
poc_mkdocs_agent/templates/        # page de rendu + widget de chat
```
