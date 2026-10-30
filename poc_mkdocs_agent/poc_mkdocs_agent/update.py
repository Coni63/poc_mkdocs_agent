import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import AsyncIterator

from fastapi.concurrency import run_in_threadpool

from .chat import SYSTEM_PROMPT, ChatError, docs_dir_of, load_docs
from .llm import LLM

MAX_ATTEMPTS = 2  # one initial try + one retry with the validation errors fed back

# Instructions live in the user message so the system + docs prefix stays identical to /question (shared cache).
UPDATE_INSTRUCTIONS = """Modifie la documentation selon la demande ci-dessous.
Réponds UNIQUEMENT avec un objet JSON de cette forme (json) :
{{"summary": "résumé court des changements", "edits": [{{"path": "chemin/du/fichier.md", "old_str": "...", "new_str": "..."}}]}}
Règles :
- "path" est le chemin relatif tel qu'indiqué après "=== FILE:" ; seuls les fichiers existants sont modifiables.
- "old_str" est copié EXACTEMENT depuis le fichier (espaces et retours à la ligne inclus) et doit y apparaître une seule fois :
  ajoute assez de contexte pour qu'il soit unique.
- "new_str" le remplace. Fais des modifications minimales, sans reformater le reste.
- Si la demande est impossible ou ne concerne pas cette documentation, renvoie "edits": [] et explique pourquoi dans "summary".

Demande : {request}"""


def validate_edits(raw: str, docs_dir: Path) -> tuple[str, dict[Path, str]]:
    """Parse the LLM answer and apply the edits in memory. Returns (summary, {file: new content})."""
    try:
        data = json.loads(raw)
        summary = str(data["summary"])
        edits = data["edits"]
        assert isinstance(edits, list)
    except (ValueError, KeyError, TypeError, AssertionError):
        raise ChatError('Réponse invalide : JSON attendu avec les clés "summary" et "edits".')

    root = docs_dir.resolve()
    contents: dict[Path, str] = {}
    errors: list[str] = []
    for i, edit in enumerate(edits, 1):
        try:
            rel, old, new = edit["path"], edit["old_str"], edit["new_str"]
        except (KeyError, TypeError):
            errors.append(f"edit {i}: clés path/old_str/new_str requises")
            continue
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or path.suffix != ".md" or not path.is_file():
            errors.append(f"edit {i}: fichier invalide ou inexistant : {rel}")
            continue
        text = contents.get(path)
        if text is None:
            text = path.read_text(encoding="utf-8")
        count = text.count(old) if old else 0
        if count != 1:
            errors.append(f"edit {i} ({rel}): old_str trouvé {count} fois, il doit apparaître exactement une fois")
            continue
        contents[path] = text.replace(old, new, 1)
    if errors:
        raise ChatError("\n".join(errors))
    return summary, contents


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=mkdocs-agent", "-c", "user.email=mkdocs-agent@localhost", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise ChatError(f"git {args[0]} a échoué : {result.stderr.strip()}")
    return result.stdout


def commit_on_branch(project_dir: Path, contents: dict[Path, str], summary: str, request: str) -> tuple[str, str]:
    """Commit the new contents on a fresh branch using a temporary worktree: the user's checkout is untouched."""
    try:
        repo = Path(git(project_dir, "rev-parse", "--show-toplevel").strip())
    except ChatError:
        raise ChatError("Le projet n'est pas un dépôt git : impossible de créer une branche.")

    slug = re.sub(r"[^a-z0-9]+", "-", request.lower()).strip("-")[:40].strip("-") or "edit"
    branch = f"agent/update-{slug}-{int(time.time())}"

    with tempfile.TemporaryDirectory(prefix="mkdocs_agent_wt_") as tmp:
        worktree = Path(tmp) / "wt"
        git(repo, "worktree", "add", "-q", "-b", branch, str(worktree), "HEAD")
        try:
            for path, text in contents.items():
                target = worktree / path.relative_to(repo.resolve())
                target.write_text(text, encoding="utf-8", newline="")
            git(worktree, "add", "-A")
            git(worktree, "commit", "-q", "-m", f"docs: {summary}\n\nDemande : {request}")
            diff = git(worktree, "show", "--format=", "--patch")
        finally:
            git(repo, "worktree", "remove", "--force", str(worktree))
    return branch, diff


async def update_docs(llm: LLM, project_dir: Path, request: str) -> AsyncIterator[str]:
    docs = load_docs(project_dir)
    docs_dir = docs_dir_of(project_dir)

    yield "Génération des modifications…\n"
    history: list[dict] = []
    question = UPDATE_INSTRUCTIONS.format(request=request)
    for attempt in range(MAX_ATTEMPTS):
        raw = await llm.complete_json(SYSTEM_PROMPT, docs, history, question)
        try:
            summary, contents = validate_edits(raw, docs_dir)
            break
        except ChatError as e:
            if attempt == MAX_ATTEMPTS - 1:
                yield f"Échec : le LLM n'a pas produit de modifications valides.\n{e}"
                return
            yield "Modifications invalides, nouvelle tentative…\n"
            history += [
                {"role": "user", "content": question},
                {"role": "assistant", "content": raw},
            ]
            question = f"Ta réponse est invalide :\n{e}\nCorrige et renvoie le JSON complet."

    if not contents:
        yield f"Aucune modification : {summary}"
        return

    try:
        branch, diff = await run_in_threadpool(commit_on_branch, project_dir, contents, summary, request)
    except ChatError as e:
        yield f"Échec : {e}"
        return
    yield f"\n{summary}\n\nBranche créée : {branch}\n\n{diff}"
