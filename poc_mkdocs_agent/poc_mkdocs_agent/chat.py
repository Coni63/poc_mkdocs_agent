import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from .llm import LLM

SYSTEM_PROMPT = (
    "Tu réponds à des questions sur la documentation ci-dessous. "
    "Appuie-toi uniquement sur cette documentation; si la réponse n'y est pas, dis-le. "
    "Cite les fichiers sources (leur chemin) utilisés pour répondre. "
    "Réponds dans la langue de la question."
)

MAX_TURNS = 10  # oldest turns are dropped beyond this (breaks the cache once, then it rebuilds)

COMMANDS_HELP = "Commandes disponibles : /question <votre question>, /update <modification souhaitée>"


class ChatError(Exception):
    pass


@dataclass
class Session:
    docs_hash: str
    history: list[dict] = field(default_factory=list)


_sessions: dict[tuple[str, str], Session] = {}


def docs_dir_of(project_dir: Path) -> Path:
    docs_dir = project_dir / "docs"
    return docs_dir if docs_dir.is_dir() else project_dir


def load_docs(project_dir: Path) -> str:
    """Concatenate every .md file in a deterministic order: the prefix must be byte-stable for caching."""
    docs_dir = docs_dir_of(project_dir)
    parts = [
        f"=== FILE: {path.relative_to(docs_dir).as_posix()} ===\n{path.read_text(encoding='utf-8')}"
        for path in sorted(docs_dir.rglob("*.md"))
    ]
    return "\n\n".join(parts)


def parse_command(message: str) -> tuple[str, str]:
    message = message.strip()
    if not message.startswith("/"):
        raise ChatError(f"Message sans commande. {COMMANDS_HELP}")
    command, _, args = message.partition(" ")
    if command not in ("/question", "/update"):
        raise ChatError(f"Commande inconnue : {command}. {COMMANDS_HELP}")
    if not args.strip():
        raise ChatError(f"Argument vide. Usage : {command} <texte>")
    return command, args.strip()


async def answer_question(
    llm: LLM, session_id: str, project: str, project_dir: Path, question: str
) -> AsyncIterator[str]:
    docs = load_docs(project_dir)
    docs_hash = hashlib.sha1(docs.encode()).hexdigest()

    key = (session_id, project)
    session = _sessions.get(key)
    if session is None or session.docs_hash != docs_hash:  # docs changed: cached prefix is stale
        session = _sessions[key] = Session(docs_hash)

    chunks: list[str] = []
    async for chunk in llm.stream(SYSTEM_PROMPT, docs, session.history, question):
        chunks.append(chunk)
        yield chunk

    # History is append-only and only recorded once the answer is complete.
    session.history += [{"role": "user", "content": question}, {"role": "assistant", "content": "".join(chunks)}]
    del session.history[: -2 * MAX_TURNS]
