"""CLI entry point and FastAPI application."""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import random
import sys
import threading
import time
import warnings
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi import status as http_status
from fastapi.responses import FileResponse, JSONResponse

from app.chat_orchestrator import ChatOrchestrator
from app.config import Settings, get_settings
from app.settings_store import (
    SettingsValidationError,
    init_settings_store,
    read_settings,
    update_settings,
)
from app.terminal_input import ChatInputSession
from app.lmstudio_client import LMStudioClient, LMStudioError, LMStudioModelError
from app import lmstudio_server_config as lm_server_cfg
from app.message_parts import UserTurn
from app.project_manager import ProjectManager, ProjectNotFoundError, ThreadNotFoundError
from app.schemas import (
    ChatResponse,
    DocFileInfo,
    HealthResponse,
    InstructionsResponse,
    InstructionsUpdate,
    MessageCreate,
    ProjectCreate,
    ProjectDetail,
    ProjectSummary,
    LmModelInfo,
    LmModelLoadRequest,
    LmModelLoadResponse,
    LmModelsResponse,
    LmServerStatus,
    LmServerUpdate,
    SettingsSnapshot,
    SettingsUpdate,
    SourcesState,
    SourcesUpdate,
    SyncResponse,
    ThreadCreate,
    ThreadRename,
    ThreadSummary,
)

# Apply local settings overrides before the first get_settings() call.
init_settings_store()

app = FastAPI(title="Prompter", description="Local project assistant with LM Studio")

_THINKING_VERBS = [
    "Accomplishing", "Actioning", "Actualizing", "Architecting", "Baking",
    "Beaming", "Befuddling", "Billowing", "Blanching", "Bloviating",
    "Boogieing", "Boondoggling", "Bootstrapping", "Brewing", "Burrowing",
    "Calculating", "Canoodling", "Caramelizing", "Cascading", "Catapulting",
    "Cerebrating", "Channeling", "Choreographing", "Churning", "Clauding",
    "Coalescing", "Cogitating", "Combobulating", "Composing", "Computing",
    "Concocting", "Considering", "Contemplating", "Cooking", "Crafting",
    "Creating", "Crunching", "Crystallizing", "Cultivating", "Deciphering",
    "Deliberating", "Determining", "Doodling", "Drizzling", "Ebbing",
    "Elucidating", "Embellishing", "Enchanting", "Envisioning", "Fermenting",
    "Finagling", "Flambéing", "Flowing", "Fluttering", "Forging",
    "Forming", "Frolicking", "Frosting", "Gallivanting", "Galloping",
    "Garnishing", "Generating", "Germinating", "Grooving", "Harmonizing",
    "Hashing", "Hatching", "Herding", "Ideating", "Imagining",
    "Improvising", "Incubating", "Inferring", "Infusing", "Julienning",
    "Kneading", "Leavening", "Levitating", "Lollygagging", "Manifesting",
    "Marinating", "Meandering", "Metamorphosing", "Moonwalking", "Moseying",
    "Mulling", "Mustering", "Musing", "Nesting", "Noodling",
    "Nucleating", "Orbiting", "Orchestrating", "Perambulating", "Percolating",
    "Perusing", "Philosophising", "Pondering", "Pontificating", "Prestidigitating",
    "Processing", "Proofing", "Propagating", "Puttering", "Puzzling",
    "Roosting", "Ruminating", "Sautéing", "Scampering", "Schlepping",
    "Seasoning", "Shenaniganing", "Shimmying", "Simmering", "Sketching",
    "Smooshing", "Spelunking", "Spinning", "Sprouting", "Stewing",
    "Sublimating", "Swirling", "Swooping", "Synthesizing", "Tempering",
    "Thinking", "Tinkering", "Transmuting", "Twisting", "Undulating",
    "Unfurling", "Unravelling", "Vibing", "Waddling", "Wandering",
    "Warping", "Whisking", "Wibbling", "Working", "Wrangling",
    "Zesting", "Zigzagging",
]

_DONE_VERBS = [
    "Brewed", "Baked", "Cogitated", "Computed", "Concocted",
    "Contemplated", "Crafted", "Deliberated", "Extrapolated", "Forged",
    "Garnished", "Generated", "Hatched", "Imagined", "Infused",
    "Marinated", "Mused", "Noodled", "Orchestrated", "Percolated",
    "Pondered", "Ruminated", "Sautéed", "Seasoned", "Simmered",
    "Synthesized", "Tinkered", "Wrangled",
]


_SPINNER_UNICODE = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_SPINNER_ASCII = ["|", "/", "-", "\\"]


class _ClaudeSpinner:
    """Claude Code-style thinking spinner with rotating funny verbs."""

    _VERB_INTERVAL = 2.0
    _FRAME_INTERVAL = 0.08

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._start_time: float = 0.0
        enc = getattr(sys.stdout, "encoding", "") or ""
        self._frames = (
            _SPINNER_UNICODE
            if enc.lower().replace("-", "") == "utf8"
            else _SPINNER_ASCII
        )

    def _spin(self) -> None:
        verb = random.choice(_THINKING_VERBS)
        last_verb_change = time.monotonic()
        max_width = 0

        for frame in itertools.cycle(self._frames):
            if self._stop.is_set():
                break
            now = time.monotonic()
            if now - last_verb_change >= self._VERB_INTERVAL:
                verb = random.choice(_THINKING_VERBS)
                last_verb_change = now
            line = f"\r{frame} {verb}..."
            max_width = max(max_width, len(line))
            sys.stdout.write(f"{line:<{max_width}}")
            sys.stdout.flush()
            self._stop.wait(self._FRAME_INTERVAL)

        elapsed = time.monotonic() - self._start_time
        past = random.choice(_DONE_VERBS)
        done_line = f"* {past} for {elapsed:.0f}s"
        sys.stdout.write(f"\r{' ' * max_width}\r")
        sys.stdout.write(f"{done_line}\n")
        sys.stdout.flush()

    def __enter__(self) -> "_ClaudeSpinner":
        self._start_time = time.monotonic()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)


def _services() -> tuple[Settings, ProjectManager, ChatOrchestrator, LMStudioClient]:
    settings = get_settings()
    projects = ProjectManager(settings)
    lm = LMStudioClient(settings)

    from app.adapters.classifier_qwen import QwenClassifierAdapter
    from app.adapters.embedding_nomic import NomicEmbeddingAdapter
    from app.adapters.qdrant_store import QdrantAdapter
    from app.model_scheduler import get_model_scheduler
    from app.router import HybridRouter

    classifier = QwenClassifierAdapter(settings)
    router = HybridRouter(settings, classifier)
    embedding = NomicEmbeddingAdapter(settings)
    vector_store = QdrantAdapter(settings)
    scheduler = get_model_scheduler(settings) if settings.ollama.scheduler_enabled else None

    orchestrator = ChatOrchestrator(
        router=router,
        vector_store=vector_store,
        embedding_service=embedding,
        vision_service=None,
        model_scheduler=scheduler,
        settings=settings,
        projects=projects,
    )
    return settings, projects, orchestrator, lm


async def _collect_chat_response(
    orchestrator: ChatOrchestrator,
    project_id: str,
    thread_id: str,
    content: str | UserTurn,
) -> ChatResponse:
    """Collect streamed SSE events into a ChatResponse for backward-compatible API."""
    reply_parts: list[str] = []
    retrieved_chunks: list[dict[str, Any]] = []
    async for event_str in orchestrator.handle_message(project_id, thread_id, content):
        event = json.loads(event_str)
        event_type = event.get("type")
        if event_type == "chunk":
            reply_parts.append(event.get("content", ""))
        elif event_type == "sources":
            retrieved_chunks = event.get("chunks", [])
        elif event_type == "error":
            raise HTTPException(
                status_code=502,
                detail=event.get("message", "Chat error"),
            )
    return ChatResponse(
        thread_id=thread_id,
        reply="".join(reply_parts),
        retrieved_chunks=retrieved_chunks,
    )


def _sanitize_upload_filename(name: str | None) -> str:
    """Strip directory components and reject dangerous filenames."""
    if not name:
        raise HTTPException(status_code=400, detail="Filename is required")
    safe = Path(name).name
    if not safe or safe in {".", ".."} or "/" in safe or "\\" in safe:
        raise HTTPException(status_code=400, detail=f"Invalid filename: {name!r}")
    return safe


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def api_health() -> HealthResponse:
    _, _, _, lm = _services()
    health = lm.health_check()
    return HealthResponse(
        ok=health.ok,
        mode=health.mode,
        base_url=health.base_url,
        model=health.model,
        model_loaded=health.model_loaded,
        message=health.message,
        available_models=health.available_models,
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/settings", response_model=SettingsSnapshot)
def api_get_settings() -> SettingsSnapshot:
    return SettingsSnapshot.model_validate(read_settings())


@app.put("/settings", response_model=SettingsSnapshot)
@app.put("/settings/", response_model=SettingsSnapshot, include_in_schema=False)
def api_update_settings(body: SettingsUpdate) -> SettingsSnapshot:
    updates = body.model_dump(exclude_none=True)
    try:
        public = update_settings(updates) if updates else read_settings()
    except SettingsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return SettingsSnapshot.model_validate(public)


# ---------------------------------------------------------------------------
# LM Studio models & server
# ---------------------------------------------------------------------------

def _lm_server_status() -> LmServerStatus:
    config, path = lm_server_cfg.read_config()
    port = int(config.get("port", 1234))
    iface = str(config.get("networkInterface", "127.0.0.1"))
    on_net = lm_server_cfg.serve_on_local_network(config)
    return LmServerStatus(
        config_found=path is not None,
        config_path=str(path) if path else None,
        port=port,
        network_interface=iface,
        serve_on_local_network=on_net,
        access_urls=lm_server_cfg.build_access_urls(port, on_network=on_net),
    )


@app.get("/lmstudio/models", response_model=LmModelsResponse)
@app.get("/lmstudio/models/", response_model=LmModelsResponse, include_in_schema=False)
def api_lmstudio_models() -> LmModelsResponse:
    settings, _, _, lm = _services()
    try:
        catalog = lm.list_models_catalog()
    except LMStudioError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return LmModelsResponse(
        models=[
            LmModelInfo(
                key=m.key,
                display_name=m.display_name,
                type=m.type,
                loaded=m.loaded,
                vision=m.vision,
                params_string=m.params_string,
            )
            for m in catalog
        ],
        selected_model=settings.lmstudio_model,
        mode=settings.lmstudio_mode,
    )


@app.post("/lmstudio/models/load", response_model=LmModelLoadResponse)
@app.post(
    "/lmstudio/models/load/",
    response_model=LmModelLoadResponse,
    include_in_schema=False,
)
def api_lmstudio_load_model(body: LmModelLoadRequest) -> LmModelLoadResponse:
    _, _, _, lm = _services()
    try:
        result = lm.load_model(body.model, context_length=body.context_length)
    except LMStudioModelError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LMStudioError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return LmModelLoadResponse(
        ok=result.ok,
        model=result.model,
        status=result.status,
        instance_id=result.instance_id,
        load_time_seconds=result.load_time_seconds,
        message=result.message,
    )


@app.get("/lmstudio/server", response_model=LmServerStatus)
def api_lmstudio_server_get() -> LmServerStatus:
    return _lm_server_status()


@app.put("/lmstudio/server", response_model=LmServerStatus)
@app.put("/lmstudio/server/", response_model=LmServerStatus, include_in_schema=False)
def api_lmstudio_server_put(body: LmServerUpdate) -> LmServerStatus:
    try:
        config, path, note = lm_server_cfg.write_serve_on_local_network(
            body.serve_on_local_network
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write config: {exc}") from exc
    port = int(config.get("port", 1234))
    iface = str(config.get("networkInterface", "127.0.0.1"))
    on_net = lm_server_cfg.serve_on_local_network(config)
    return LmServerStatus(
        config_found=True,
        config_path=str(path),
        port=port,
        network_interface=iface,
        serve_on_local_network=on_net,
        access_urls=lm_server_cfg.build_access_urls(port, on_network=on_net),
        restart_required_note=note,
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.post("/projects/init", response_model=ProjectDetail, status_code=http_status.HTTP_201_CREATED)
def api_init_project(body: ProjectCreate) -> ProjectDetail:
    _, projects, _, _ = _services()
    try:
        return projects.init_project(body.name, instructions=body.system_prompt or None)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/projects", response_model=ProjectDetail, status_code=http_status.HTTP_201_CREATED)
def api_create_project(body: ProjectCreate) -> ProjectDetail:
    _, projects, _, _ = _services()
    try:
        return projects.create_project(
            body.name,
            body.system_prompt,
            config=body.config,
        )
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/projects", response_model=list[ProjectSummary])
def api_list_projects() -> list[ProjectSummary]:
    _, projects, _, _ = _services()
    return projects.list_projects()


@app.get("/projects/{project_id}", response_model=ProjectDetail)
def api_get_project(project_id: str) -> ProjectDetail:
    _, projects, _, _ = _services()
    try:
        return projects.get_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

@app.get("/projects/{project_id}/instructions", response_model=InstructionsResponse)
def api_get_instructions(project_id: str) -> InstructionsResponse:
    _, projects, _, _ = _services()
    try:
        content = projects.read_system_prompt(project_id)
        return InstructionsResponse(content=content)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/projects/{project_id}/instructions", response_model=InstructionsResponse)
def api_update_instructions(project_id: str, body: InstructionsUpdate) -> InstructionsResponse:
    _, projects, _, _ = _services()
    try:
        projects.update_system_prompt(project_id, body.content)
        return InstructionsResponse(content=body.content)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@app.post("/projects/{project_id}/sync", response_model=SyncResponse)
def api_sync(project_id: str) -> SyncResponse:
    _, projects, _, _ = _services()
    try:
        ingested = projects.sync_docs(project_id)
        return SyncResponse(chunk_count=len(ingested))
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Sources (NotebookLM-style)
# ---------------------------------------------------------------------------

@app.get("/projects/{project_id}/sources", response_model=SourcesState)
def api_get_sources(project_id: str) -> SourcesState:
    _, projects, _, _ = _services()
    try:
        return projects.list_doc_files(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/projects/{project_id}/sources", response_model=SourcesState)
def api_update_sources(project_id: str, body: SourcesUpdate) -> SourcesState:
    _, projects, _, _ = _services()
    try:
        projects.set_enabled_sources(
            project_id,
            body.enabled,
            default_new_enabled=body.default_new_enabled,
        )
        return projects.list_doc_files(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/projects/{project_id}/sources",
    response_model=SourcesState,
    status_code=http_status.HTTP_201_CREATED,
)
async def api_upload_sources(
    project_id: str,
    files: list[UploadFile] = File(...),
) -> SourcesState:
    settings, projects, _, _ = _services()
    try:
        projects._require_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    src_data = projects._load_sources_json(project_id)
    default_new_enabled: bool = src_data.get("default_new_enabled", True)
    enabled_list: list[str] = list(src_data.get("enabled", []))

    tmp_dir = settings.data_dir / ".uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    for upload in files:
        safe_name = _sanitize_upload_filename(upload.filename)
        tmp_file = tmp_dir / safe_name
        tmp_file.write_bytes(await upload.read())
        try:
            projects.add_file(project_id, tmp_file)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            tmp_file.unlink(missing_ok=True)

        # Empty enabled_list means all docs are on; only append in explicit selection mode.
        if default_new_enabled and enabled_list and safe_name not in enabled_list:
            enabled_list.append(safe_name)

    if default_new_enabled and enabled_list:
        projects.set_enabled_sources(
            project_id, enabled_list, default_new_enabled=default_new_enabled
        )

    return projects.list_doc_files(project_id)


@app.delete("/projects/{project_id}/sources/{filename}", status_code=http_status.HTTP_204_NO_CONTENT)
def api_delete_source(project_id: str, filename: str) -> None:
    safe_name = _sanitize_upload_filename(filename)
    _, projects, _, _ = _services()
    try:
        projects.delete_doc_file(project_id, safe_name)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Files (legacy upload endpoint kept for backward compat)
# ---------------------------------------------------------------------------

@app.post(
    "/projects/{project_id}/files",
    status_code=http_status.HTTP_201_CREATED,
)
def api_add_file(project_id: str, upload: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a file into the project's ``docs/`` folder and ingest it."""
    _, projects, _, _ = _services()

    settings = get_settings()
    safe_name = _sanitize_upload_filename(upload.filename)
    tmp_dir = settings.data_dir / ".uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_dir / safe_name
    dest.write_bytes(upload.file.read())

    try:
        chunks = projects.add_file(project_id, dest)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        dest.unlink(missing_ok=True)

    src_data = projects._load_sources_json(project_id)
    default_new_enabled: bool = src_data.get("default_new_enabled", True)
    enabled_list: list[str] = list(src_data.get("enabled", []))
    if default_new_enabled and enabled_list and safe_name not in enabled_list:
        enabled_list.append(safe_name)
        projects.set_enabled_sources(
            project_id, enabled_list, default_new_enabled=default_new_enabled
        )

    detail = projects.get_project(project_id)
    return {
        "ingested_chunks": chunks,
        "docs_path": detail.docs_path,
        "note": "You can also drop files directly into docs/ and run sync or chat.",
    }


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------

@app.get("/projects/{project_id}/threads", response_model=list[ThreadSummary])
def api_list_threads(project_id: str) -> list[ThreadSummary]:
    _, projects, _, _ = _services()
    try:
        return projects.list_threads(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/projects/{project_id}/threads",
    response_model=ThreadSummary,
    status_code=http_status.HTTP_201_CREATED,
)
def api_create_thread(project_id: str, body: ThreadCreate) -> ThreadSummary:
    _, projects, _, _ = _services()
    try:
        return projects.create_thread(project_id, body.title)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(
    "/projects/{project_id}/threads/{thread_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def api_delete_thread(project_id: str, thread_id: str) -> None:
    _, projects, _, _ = _services()
    try:
        projects.delete_thread(project_id, thread_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(
    "/projects/{project_id}/threads/{thread_id}/messages",
    response_model=ThreadSummary,
)
def api_clear_thread_messages(project_id: str, thread_id: str) -> ThreadSummary:
    _, projects, _, _ = _services()
    try:
        return projects.clear_thread_messages(project_id, thread_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch(
    "/projects/{project_id}/threads/{thread_id}",
    response_model=ThreadSummary,
)
def api_rename_thread(
    project_id: str, thread_id: str, body: ThreadRename
) -> ThreadSummary:
    _, projects, _, _ = _services()
    try:
        return projects.rename_thread(project_id, thread_id, body.title)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@app.get("/projects/{project_id}/threads/{thread_id}/messages")
def api_get_messages(project_id: str, thread_id: str) -> list[dict[str, Any]]:
    _, projects, _, _ = _services()
    try:
        return projects.get_thread_messages(project_id, thread_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/projects/{project_id}/threads/{thread_id}/messages",
    response_model=ChatResponse,
)
async def api_send_message(
    project_id: str,
    thread_id: str,
    body: MessageCreate,
) -> ChatResponse:
    _, projects, orchestrator, _ = _services()
    try:
        return await _collect_chat_response(
            orchestrator, project_id, thread_id, body.content
        )
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Static SPA (GET only — do not mount StaticFiles at "/" or PUT /settings → 405)
# ---------------------------------------------------------------------------

_WEB_DIST = Path(__file__).parent.parent / "web" / "dist"


def _register_spa() -> None:
    if not _WEB_DIST.exists():
        @app.get("/", include_in_schema=False)
        def spa_missing() -> JSONResponse:
            return JSONResponse({
                "hint": "Web UI not built. Run: cd web && npm install && npm run build",
                "docs": "/docs",
            })
        return

    from starlette.staticfiles import StaticFiles

    assets_dir = _WEB_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="spa-assets")

    @app.get("/", include_in_schema=False)
    def spa_index() -> FileResponse:
        return FileResponse(_WEB_DIST / "index.html")

    @app.get("/{spa_path:path}", include_in_schema=False)
    def spa_path(spa_path: str) -> FileResponse:
        # Never serve the SPA for API paths (avoids swallowing /settings etc.)
        root = spa_path.split("/", 1)[0]
        if root in {"health", "settings", "projects", "lmstudio", "docs", "openapi.json"}:
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = _WEB_DIST / spa_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_WEB_DIST / "index.html")


_register_spa()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _join_project_ref(parts: list[str]) -> str:
    return " ".join(parts).strip()


def _resolve_cli_project(projects: ProjectManager, parts: list[str]) -> str:
    ref = _join_project_ref(parts)
    if not ref:
        raise ProjectNotFoundError("project reference required")
    return projects.resolve_project_ref(ref)


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prompter",
        description="Local project assistant (LM Studio backend)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Check LM Studio connectivity and model")

    init = sub.add_parser("init", help="Scaffold a new project folder under projects/")
    init.add_argument("name", help="Project display name")
    init.add_argument("--project-id", help="Override auto-generated project id")

    create = sub.add_parser(
        "create-project",
        help="(Deprecated) Use init instead",
    )
    create.add_argument("name", help="Project display name")
    create.add_argument(
        "--system-prompt",
        help="System prompt text or path to a .txt/.md file",
    )
    create.add_argument("--project-id", help="Override auto-generated project id")

    sync = sub.add_parser("sync", help="Ingest new/changed files from docs/")
    sync.add_argument("project_ref", nargs="+", metavar="project")

    add = sub.add_parser("add-file", help="Copy a document into docs/ and ingest")
    add.add_argument(
        "path_args",
        nargs="+",
        metavar="project file",
        help="Project reference (one or more words) and file path",
    )

    sub.add_parser("list-projects", help="List all projects")

    show = sub.add_parser("show-project", help="Show project details")
    show.add_argument("project_ref", nargs="+", metavar="project")

    thread = sub.add_parser("new-thread", help="Create a new chat thread")
    thread.add_argument("project_ref", nargs="+", metavar="project")
    thread.add_argument("--title", default=None)

    chat_cmd = sub.add_parser("chat", help="Interactive chat (syncs docs/ first)")
    chat_cmd.add_argument("project_ref", nargs="+", metavar="project")
    chat_cmd.add_argument("--thread-id", help="Existing thread id (creates one if omitted)")

    serve_cmd = sub.add_parser("serve", help="Start the web server and open browser")
    serve_cmd.add_argument("--no-browser", action="store_true", help="Don't open browser")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8000)

    return parser


def _load_system_prompt(value: str | None) -> str | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return value


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_cli_parser()
    args = parser.parse_args(argv)
    settings, projects, chat, lm = _services()

    try:
        if args.command == "health":
            health = lm.health_check()
            print(f"ok={health.ok} mode={health.mode} model={health.model}")
            print(health.message)
            if health.available_models:
                print("available models (sample):", ", ".join(health.available_models[:10]))
            return 0 if health.ok else 1

        if args.command == "init":
            detail = projects.init_project(args.name, project_id=args.project_id)
            print(f"Created project '{detail.name}' id={detail.id}")
            print(f"Instructions: {detail.instructions_path}")
            print(f"Docs folder:    {detail.docs_path}")
            print("Add files to docs/, edit instructions.md, then: chat", detail.id)
            return 0

        if args.command == "create-project":
            warnings.warn(
                "create-project is deprecated; use: python -m app.main init",
                DeprecationWarning,
                stacklevel=1,
            )
            prompt = _load_system_prompt(args.system_prompt)
            detail = projects.init_project(
                args.name,
                project_id=args.project_id,
                instructions=prompt,
            )
            print(f"Created project '{detail.name}' id={detail.id}")
            print(f"Instructions: {detail.instructions_path}")
            print(f"Docs folder:    {detail.docs_path}")
            return 0

        if args.command == "sync":
            project_id = _resolve_cli_project(projects, args.project_ref)
            ingested = projects.sync_docs(project_id)
            print(f"Synced docs/: {len(ingested)} new/updated chunk(s)")
            return 0

        if args.command == "add-file":
            if len(args.path_args) < 2:
                print(
                    "error: add-file requires a project reference and file path",
                    file=sys.stderr,
                )
                return 1
            file_path = Path(args.path_args[-1])
            project_id = _resolve_cli_project(projects, args.path_args[:-1])
            chunks = projects.add_file(project_id, file_path)
            print(f"Ingested {len(chunks)} chunks from {file_path.name}")
            return 0

        if args.command == "list-projects":
            items = projects.list_projects()
            if not items:
                print("No projects yet. Run: python -m app.main init \"My Project\"")
                return 0
            for item in items:
                print(
                    f"{item.id}\t{item.name}\tfiles={item.file_count}\tthreads={item.thread_count}"
                )
            return 0

        if args.command == "show-project":
            project_id = _resolve_cli_project(projects, args.project_ref)
            detail = projects.get_project(project_id)
            print(f"id: {detail.id}")
            print(f"name: {detail.name}")
            print(f"instructions: {detail.instructions_path}")
            print(f"docs: {detail.docs_path}")
            print(f"system_prompt:\n{detail.system_prompt}")
            return 0

        if args.command == "new-thread":
            project_id = _resolve_cli_project(projects, args.project_ref)
            thread = projects.create_thread(project_id, args.title)
            print(f"thread_id={thread.id}")
            return 0

        if args.command == "chat":
            project_id = _resolve_cli_project(projects, args.project_ref)
            ingested = projects.sync_docs(project_id)
            if ingested:
                print(f"Synced {len(ingested)} new/updated chunk(s) from docs/")

            thread_id = args.thread_id
            if not thread_id:
                thread = projects.create_thread(project_id, title="cli-chat")
                thread_id = thread.id
                print(f"Started thread {thread_id}")

            ChatInputSession.print_hints()
            print(f"Chatting in project '{project_id}'.")
            project_root = projects.projects_root / project_id
            session = ChatInputSession(
                project_id=project_id,
                project_root=project_root,
                project_manager=projects,
            )
            while True:
                try:
                    user_turn = session.read_message()
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                if user_turn is None:
                    break
                if user_turn.is_empty:
                    continue
                try:
                    async def _stream_reply() -> str:
                        parts: list[str] = []
                        async for event_str in chat.handle_message(
                            project_id, thread_id, user_turn
                        ):
                            event = json.loads(event_str)
                            if event.get("type") == "chunk":
                                parts.append(event.get("content", ""))
                            elif event.get("type") == "error":
                                raise RuntimeError(
                                    event.get("message", "Chat error")
                                )
                        return "".join(parts)

                    with _ClaudeSpinner():
                        reply = asyncio.run(_stream_reply())
                except (LMStudioError, RuntimeError) as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    continue
                print(f"• {reply}")
            return 0

        if args.command == "serve":
            import uvicorn

            host: str = args.host
            port: int = args.port

            if not args.no_browser:
                import webbrowser

                def _open_browser() -> None:
                    time.sleep(1.5)
                    webbrowser.open(f"http://{host}:{port}")

                threading.Thread(target=_open_browser, daemon=True).start()

            uvicorn.run("app.main:app", host=host, port=port)
            return 0

    except ProjectNotFoundError as exc:
        print(f"error: project not found: {exc}", file=sys.stderr)
        return 1
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    parser.print_help()
    return 1


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
