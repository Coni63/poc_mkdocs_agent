import html
import json
import shutil
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .chat import ChatError, answer_question, parse_command
from .llm import DeepSeekLLM
from .update import update_docs

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data.json"

# Temporary build directory, removed when the app stops.
BUILD_ROOT = Path(tempfile.mkdtemp(prefix="mkdocs_agent_"))

PROJECT_TEMPLATE = (Path(__file__).parent / "templates" / "project.html").read_text(encoding="utf-8")

_llm: DeepSeekLLM | None = None


def get_llm() -> DeepSeekLLM:
    global _llm
    if _llm is None:
        _llm = DeepSeekLLM()
    return _llm


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    shutil.rmtree(BUILD_ROOT, ignore_errors=True)


app = FastAPI(title="MkDocs Agent POC", lifespan=lifespan)


def load_projects() -> dict[str, Path]:
    """Read data.json: {name: path}, relative paths resolve from data.json."""
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {name: (ROOT / path).resolve() for name, path in data.items()}


def build_site(name: str, project_dir: Path) -> Path:
    """Build the MkDocs site of a project into a temp dir and return it."""
    out_dir = BUILD_ROOT / name
    shutil.rmtree(out_dir, ignore_errors=True)

    config = project_dir / "mkdocs.yml"
    if not config.exists():
        # Project without mkdocs.yml: generate a minimal config outside the project.
        config = BUILD_ROOT / f"{name}.mkdocs.yml"
        config.write_text(
            f"site_name: {json.dumps(name)}\n"
            f"docs_dir: {json.dumps(str(project_dir / 'docs'))}\n",
            encoding="utf-8",
        )

    result = subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "-q", "-f", str(config), "-d", str(out_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    return out_dir


PAGE_STYLE = """
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, sans-serif; background: #f5f6f8; color: #222; }
</style>
"""


@app.get("/", response_class=HTMLResponse)
def home():
    items = "".join(
        f'<li><a href="/project/{html.escape(name)}">{html.escape(name)}</a>'
        f"<small>{html.escape(str(path))}</small></li>"
        for name, path in load_projects().items()
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>MkDocs Agent</title>
{PAGE_STYLE}
<style>
  main {{ max-width: 560px; margin: 10vh auto; padding: 0 1rem; }}
  ul {{ list-style: none; padding: 0; }}
  li a {{ display: block; padding: 1rem; margin-bottom: .6rem; background: #fff;
          border: 1px solid #dde; border-radius: 8px; color: #1a56db; text-decoration: none; font-weight: 600; }}
  li a:hover {{ border-color: #1a56db; }}
  small {{ display: none; }}
  #loading {{ display: none; text-align: center; color: #666; }}
</style></head><body><main>
<h1>Choisir un projet</h1>
<ul>{items}</ul>
<p id="loading">Build du site en cours…</p>
</main>
<script>
  document.querySelectorAll("li a").forEach(a => a.addEventListener("click", () => {{
    document.getElementById("loading").style.display = "block";
  }}));
</script></body></html>"""


@app.get("/project/{name}", response_class=HTMLResponse)
async def project(name: str):
    projects = load_projects()
    if name not in projects:
        raise HTTPException(404, "Projet inconnu")
    try:
        await run_in_threadpool(build_site, name, projects[name])
    except RuntimeError as e:
        return HTMLResponse(
            f"<h1>Échec du build</h1><pre>{html.escape(str(e))}</pre><a href='/'>Retour</a>",
            status_code=500,
        )
    return PROJECT_TEMPLATE.replace("__NAME__", html.escape(name))


class ChatRequest(BaseModel):
    session_id: str
    message: str


@app.post("/api/chat/{name}")
async def chat(name: str, req: ChatRequest):
    projects = load_projects()
    if name not in projects:
        raise HTTPException(404, "Projet inconnu")
    try:
        command, arg = parse_command(req.message)
    except ChatError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        llm = get_llm()
    except RuntimeError as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    async def stream():
        try:
            if command == "/question":
                chunks = answer_question(llm, req.session_id, name, projects[name], arg)
            else:
                chunks = update_docs(llm, projects[name], arg)
            async for chunk in chunks:
                yield chunk
        except Exception as e:  # headers are already sent: report the failure inside the stream
            yield f"\n\n[Erreur LLM : {e}]"

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")


# Built sites are served from the temp dir (html=True serves index.html for directories).
app.mount("/site", StaticFiles(directory=BUILD_ROOT, html=True), name="site")
