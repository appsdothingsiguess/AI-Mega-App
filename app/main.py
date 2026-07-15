"""CLI entry point and FastAPI application."""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import random
import sys
import threading
import time
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi import status as http_status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.chat_orchestrator import ChatOrchestrator
from app.config import Settings, get_settings
from app.config_validation import _parse_litellm_config, validate_config
from app.logging_setup import configure_logging
from app.turn_tracker import TurnTracker
from app.settings_store import (
    SettingsValidationError,
    init_settings_store,
    read_settings,
    update_settings,
)
from app.terminal_input import ChatInputSession
from app.message_parts import UserTurn
from app.project_manager import ProjectManager, ProjectNotFoundError, ThreadNotFoundError
from app.schemas import (
    ChatStreamRequest,
    DocFileInfo,
    InstructionsResponse,
    OllamaModelsResponse,
    InstructionsUpdate,
    ProjectCreate,
    ProjectDetail,
    ProjectSummary,
    SettingsSnapshot,
    SettingsUpdate,
    SourcesState,
    SourcesUpdate,
    SyncResponse,
    ThreadCreate,
    ThreadRename,
    ThreadSummary,
    ToolsListResponse,
)

# Stable tool ids used by router rules / classifier (see settings classifier prompt).
KNOWN_ROUTER_TOOLS: tuple[str, ...] = (
    "web_search",
    "bash",
    "pdf_gen",
    "file_ops",
    "vision",
)

# Apply local settings overrides before the first get_settings() call.
init_settings_store()

logger = logging.getLogger(__name__)

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


def _build_services(
    settings: Settings | None = None,
    turn_tracker: TurnTracker | None = None,
) -> tuple[Settings, ProjectManager, ChatOrchestrator, Any]:
    settings = settings or get_settings()
    projects = ProjectManager(settings)

    from app.adapters.classifier_qwen import QwenClassifierAdapter
    from app.adapters.embedding_nomic import NomicEmbeddingAdapter
    from app.adapters.qdrant_store import QdrantAdapter
    from app.adapters.search_ddg import DuckDuckGoSearchAdapter
    from app.model_scheduler import get_model_scheduler
    from app.router import HybridRouter

    classifier = QwenClassifierAdapter(settings)
    router = HybridRouter(settings, classifier)
    embedding = NomicEmbeddingAdapter(settings)
    vector_store = QdrantAdapter(settings)
    scheduler = get_model_scheduler(settings) if settings.ollama.scheduler_enabled else None
    search = DuckDuckGoSearchAdapter()

    orchestrator = ChatOrchestrator(
        router=router,
        vector_store=vector_store,
        embedding_service=embedding,
        vision_service=None,
        search_service=search,
        model_scheduler=scheduler,
        settings=settings,
        projects=projects,
        turn_tracker=turn_tracker,
    )
    return settings, projects, orchestrator, vector_store


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)  # FIRST — before any other init

    turn_tracker = TurnTracker()
    app.state.turn_tracker = turn_tracker

    errors, warnings = await validate_config(settings)
    startup_errors = [
        err for err in errors if not err.startswith("Ollama unreachable")
    ]
    for err in errors:
        if err.startswith("Ollama unreachable"):
            warnings.append(err)
    for warning in warnings:
        logger.warning("%s", warning)
    if startup_errors:
        raise RuntimeError("; ".join(startup_errors))

    settings, projects, orchestrator, vector_store = _build_services(
        settings, turn_tracker=turn_tracker
    )
    app.state.settings = settings
    app.state.projects = projects
    app.state.orchestrator = orchestrator
    app.state.vector_store = vector_store

    # Classifier must warm via adapter options; do not use generic scheduler warmup.
    classifier = getattr(getattr(orchestrator, "router", None), "classifier", None)
    if classifier is not None and hasattr(classifier, "warmup"):
        try:
            await classifier.warmup()
        except Exception as exc:  # noqa: BLE001 - Ollama down must not block startup
            logger.warning("Classifier warmup failed at startup: %s", exc)

    scheduler = getattr(orchestrator, "model_scheduler", None)
    if scheduler is not None:
        try:
            await scheduler.warmup_resident()
        except Exception as exc:  # noqa: BLE001 - Ollama down must not block startup
            logger.warning("Embedding warmup failed at startup: %s", exc)

    yield

    await vector_store.close()


app = FastAPI(
    title="Prompter",
    description="Local project assistant",
    lifespan=lifespan,
)


def _services() -> tuple[Settings, ProjectManager, ChatOrchestrator]:
    if (
        getattr(app.state, "orchestrator", None) is not None
        and getattr(app.state, "settings", None) is not None
    ):
        return (
            app.state.settings,
            app.state.projects,
            app.state.orchestrator,
        )
    settings, projects, orchestrator, _vector_store = _build_services()
    return settings, projects, orchestrator


def _apply_runtime_settings(fresh: Settings) -> None:
    """Propagate reloaded settings to cached app.state service objects."""
    app.state.settings = fresh
    orchestrator = getattr(app.state, "orchestrator", None)
    if orchestrator is not None:
        orchestrator.settings = fresh
        router = getattr(orchestrator, "router", None)
        if router is not None:
            router.settings = fresh


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

async def _check_ollama_health(settings: Settings) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{settings.ollama.base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            loaded_models = [
                str(entry.get("name", ""))
                for entry in data.get("models", [])
                if entry.get("name")
            ]
            return {"status": "up", "loaded_models": loaded_models}
    except Exception as exc:
        return {"status": "down", "error": str(exc), "loaded_models": []}


async def _check_qdrant_health(settings: Settings) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{settings.qdrant.url.rstrip('/')}/collections")
            resp.raise_for_status()
            data = resp.json()
            collections = data.get("result", {}).get("collections", [])
            count = len(collections) if isinstance(collections, list) else 0
            return {"status": "up", "collections": count}
    except Exception as exc:
        return {"status": "down", "error": str(exc), "collections": 0}


def _check_litellm_health(settings: Settings) -> dict[str, Any]:
    try:
        litellm_models = _parse_litellm_config(settings.litellm_config_path)
        missing = [
            alias
            for _intent, alias in settings.models.items()
            if alias not in litellm_models
        ]
        if missing:
            return {
                "status": "down",
                "error": f"Missing litellm aliases: {', '.join(missing)}",
            }
        return {"status": "up"}
    except Exception as exc:
        return {"status": "down", "error": str(exc)}


async def _check_remote_provider_health(settings: Settings) -> dict[str, Any]:
    if not settings.opencode_go.enabled:
        return {"status": "down", "error": "Remote provider disabled"}
    if not settings.opencode_go.api_key:
        return {"status": "down", "error": "API key not configured"}
    try:
        base = settings.opencode_go.base_url.rstrip("/")
        url = f"{base}/zen/go/v1/models"
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(
                url,
                headers={"Authorization": f"Bearer {settings.opencode_go.api_key}"},
            )
            if resp.status_code < 500:
                return {"status": "up"}
            return {"status": "down", "error": f"HTTP {resp.status_code}"}
    except Exception as exc:
        return {"status": "down", "error": str(exc)}


def _compute_overall_health_status(
    settings: Settings,
    services: dict[str, dict[str, Any]],
) -> str:
    if services["litellm"]["status"] != "up":
        return "down"

    ollama_up = services["ollama"]["status"] == "up"
    qdrant_up = services["qdrant"]["status"] == "up"
    remote_up = services["remote_provider"]["status"] == "up"

    has_local = any(alias.startswith("local/") for alias in settings.models.values())
    has_remote = any(alias.startswith("remote/") for alias in settings.models.values())

    local_usable = ollama_up and has_local
    remote_usable = remote_up and has_remote

    if not local_usable and not remote_usable:
        return "down"

    if ollama_up and qdrant_up and remote_up:
        return "healthy"

    return "degraded"


async def _build_health_report(settings: Settings) -> tuple[str, dict[str, Any]]:
    services = {
        "ollama": await _check_ollama_health(settings),
        "qdrant": await _check_qdrant_health(settings),
        "litellm": _check_litellm_health(settings),
        "remote_provider": await _check_remote_provider_health(settings),
    }
    status = _compute_overall_health_status(settings, services)
    return status, services


@app.get("/health")
async def api_health() -> JSONResponse:
    settings, _, _ = _services()
    status, services = await _build_health_report(settings)
    body = {"status": status, "services": services}
    code = http_status.HTTP_200_OK if status == "healthy" else http_status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=body, status_code=code)


# ---------------------------------------------------------------------------
# Debug
# ---------------------------------------------------------------------------

@app.get("/debug/last-turn")
async def api_debug_last_turn() -> JSONResponse:
    settings, _, _ = _services()
    if not settings.debug.sse_trace:
        raise HTTPException(status_code=403, detail="Debug endpoints disabled. Enable settings.debug.sse_trace.")
    tracker: TurnTracker = app.state.turn_tracker
    record = tracker.last()
    if record is None:
        return JSONResponse(status_code=204, content=None)
    return JSONResponse(tracker.to_json(record))


@app.get("/debug/turns")
async def api_debug_turns() -> JSONResponse:
    settings, _, _ = _services()
    if not settings.debug.sse_trace:
        raise HTTPException(status_code=403, detail="Debug endpoints disabled. Enable settings.debug.sse_trace.")
    tracker: TurnTracker = app.state.turn_tracker
    return JSONResponse([tracker.to_json(r) for r in tracker.all()])


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/ollama/models", response_model=OllamaModelsResponse)
async def api_ollama_models() -> OllamaModelsResponse:
    settings, _, _ = _services()
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{settings.ollama.base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
            data = resp.json()
            models = [
                str(entry.get("name", ""))
                for entry in data.get("models", [])
                if entry.get("name")
            ]
            return OllamaModelsResponse(reachable=True, models=models)
    except Exception:
        return OllamaModelsResponse(reachable=False, models=[])


@app.get("/tools", response_model=ToolsListResponse)
def api_list_tools() -> ToolsListResponse:
    return ToolsListResponse(tools=list(KNOWN_ROUTER_TOOLS))


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
    fresh = get_settings()
    if getattr(app.state, "settings", None) is not None:
        _apply_runtime_settings(fresh)
    return SettingsSnapshot.model_validate(public)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.post("/projects/init", response_model=ProjectDetail, status_code=http_status.HTTP_201_CREATED)
def api_init_project(body: ProjectCreate) -> ProjectDetail:
    _, projects, _ = _services()
    try:
        return projects.init_project(body.name, instructions=body.system_prompt or None)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/projects", response_model=ProjectDetail, status_code=http_status.HTTP_201_CREATED)
def api_create_project(body: ProjectCreate) -> ProjectDetail:
    _, projects, _ = _services()
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
    _, projects, _ = _services()
    return projects.list_projects()


@app.get("/projects/{project_id}", response_model=ProjectDetail)
def api_get_project(project_id: str) -> ProjectDetail:
    _, projects, _ = _services()
    try:
        return projects.get_project(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

@app.get("/projects/{project_id}/instructions", response_model=InstructionsResponse)
def api_get_instructions(project_id: str) -> InstructionsResponse:
    _, projects, _ = _services()
    try:
        content = projects.read_system_prompt(project_id)
        return InstructionsResponse(content=content)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/projects/{project_id}/instructions", response_model=InstructionsResponse)
def api_update_instructions(project_id: str, body: InstructionsUpdate) -> InstructionsResponse:
    _, projects, _ = _services()
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
    _, projects, _ = _services()
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
    _, projects, _ = _services()
    try:
        return projects.list_doc_files(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/projects/{project_id}/sources", response_model=SourcesState)
def api_update_sources(project_id: str, body: SourcesUpdate) -> SourcesState:
    _, projects, _ = _services()
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
    settings, projects, _ = _services()
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
    _, projects, _ = _services()
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
    _, projects, _ = _services()

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
    _, projects, _ = _services()
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
    _, projects, _ = _services()
    try:
        return projects.create_thread(project_id, body.title)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(
    "/projects/{project_id}/threads/{thread_id}",
    status_code=http_status.HTTP_204_NO_CONTENT,
)
def api_delete_thread(project_id: str, thread_id: str) -> None:
    _, projects, _ = _services()
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
    _, projects, _ = _services()
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
    _, projects, _ = _services()
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
    _, projects, _ = _services()
    try:
        return projects.get_thread_messages(project_id, thread_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/chat/{project_id}/{thread_id}")
async def api_chat_sse(
    request: Request,
    project_id: str,
    thread_id: str,
    body: ChatStreamRequest,
) -> StreamingResponse:
    _, projects, orchestrator = _services()
    try:
        projects.get_thread_messages(project_id, thread_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    disconnect_event = asyncio.Event()

    async def _watch_disconnect() -> None:
        try:
            while True:
                if await request.is_disconnected():
                    disconnect_event.set()
                    return
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise

    watch_task = asyncio.create_task(_watch_disconnect())

    async def event_generator() -> AsyncIterator[str]:
        try:
            async for event in orchestrator.handle_message(
                project_id,
                thread_id,
                body.content,
                model_override=body.model_override,
                enabled_tools=body.enabled_tools,
                disconnect_event=disconnect_event,
            ):
                try:
                    yield f"data: {event}\n\n"
                except (asyncio.CancelledError, GeneratorExit):
                    disconnect_event.set()
                    raise
        except asyncio.CancelledError:
            logger.info(
                "Client disconnected from SSE stream project=%s thread=%s",
                project_id,
                thread_id,
            )
            raise
        except Exception:
            logger.exception(
                "Unhandled exception in SSE stream project=%s thread=%s",
                project_id,
                thread_id,
            )
            yield f'data: {json.dumps({"type": "error", "message": "Internal server error"})}\n\n'
            yield f'data: {json.dumps({"type": "done", "usage": {}})}\n\n'
        finally:
            disconnect_event.set()
            watch_task.cancel()
            with suppress(asyncio.CancelledError):
                await watch_task

    return StreamingResponse(event_generator(), media_type="text/event-stream")


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

    _SPA_NO_CACHE = {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
    }

    @app.get("/", include_in_schema=False)
    def spa_index() -> FileResponse:
        # index.html must not be cached — it points at content-hashed asset URLs.
        return FileResponse(_WEB_DIST / "index.html", headers=_SPA_NO_CACHE)

    @app.get("/{spa_path:path}", include_in_schema=False)
    def spa_path(spa_path: str) -> FileResponse:
        # Never serve the SPA for API paths (avoids swallowing /settings etc.)
        root = spa_path.split("/", 1)[0]
        if root in {
            "health",
            "settings",
            "projects",
            "docs",
            "openapi.json",
            "api",
            "ollama",
            "tools",
        }:
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = _WEB_DIST / spa_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_WEB_DIST / "index.html", headers=_SPA_NO_CACHE)


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
        description="Local project assistant",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Check service health (Ollama, Qdrant, LiteLLM, remote)")

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
    settings, projects, chat = _services()

    try:
        if args.command == "health":
            status, services = asyncio.run(_build_health_report(settings))
            print(f"status={status}")
            for name, svc in services.items():
                line = f"{name}: {svc.get('status', 'unknown')}"
                if svc.get("error"):
                    line += f" ({svc['error']})"
                print(line)
            return 0 if status == "healthy" else 1

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
                except RuntimeError as exc:
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
                    webbrowser.open(f"http://{host}:{port}/")

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
