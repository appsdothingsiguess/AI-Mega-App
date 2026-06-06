"""Filesystem-backed project and thread persistence."""



from __future__ import annotations



import difflib

import json

import shutil

from datetime import datetime

from pathlib import Path

from typing import Any



import yaml



from app.config import Settings

from app.rag import (

    DOCS_DIRNAME,

    PROMPTER_DIRNAME,

    ChunkStore,

)

from app.schemas import DocFileInfo, ProjectDetail, ProjectSummary, SourcesState, ThreadSummary

from app.utils import ensure_dir, read_text_file, slugify, utc_now



PROJECT_YAML = "project.yaml"

LEGACY_METADATA_YAML = "metadata.yaml"

INSTRUCTIONS_FILE = "instructions.md"

THREADS_DIRNAME = "threads"

LEGACY_THREADS_DIRNAME = "threads"



INSTRUCTIONS_TEMPLATE = """\

# Project instructions



Edit this file to set how the assistant should behave for this project.

These instructions are loaded on every chat and sync.



- Be helpful and accurate.

- Prefer answers grounded in files under `docs/`.

- Say when the documents do not contain enough information.

- If your workflow includes a clarification phase (e.g. confirming requirements), ask only for what is still missing. After the user has confirmed the required details, proceed to the main task and do not repeat the same questions unless they change requirements.

"""





class ProjectNotFoundError(FileNotFoundError):

    pass





class ThreadNotFoundError(FileNotFoundError):

    pass





def _parse_dt(value: str | datetime) -> datetime:

    if isinstance(value, datetime):

        return value

    return datetime.fromisoformat(str(value))





class ProjectManager:

    """Manages projects as folders under ``PROJECTS_DIR/{project_id}/``."""



    def __init__(self, settings: Settings) -> None:

        self.settings = settings

        self._root = settings.effective_projects_dir

        ensure_dir(self._root)



    @property

    def projects_root(self) -> Path:

        return self._root



    def _project_dir(self, project_id: str) -> Path:

        return self._root / project_id



    def _project_yaml_path(self, project_id: str) -> Path:

        return self._project_dir(project_id) / PROJECT_YAML



    def _legacy_metadata_path(self, project_id: str) -> Path:

        return self._project_dir(project_id) / LEGACY_METADATA_YAML



    def _instructions_path(self, project_id: str) -> Path:

        return self._project_dir(project_id) / INSTRUCTIONS_FILE



    def _docs_dir(self, project_id: str) -> Path:

        return self._project_dir(project_id) / DOCS_DIRNAME



    def _threads_dir(self, project_id: str) -> Path:

        root = self._project_dir(project_id)

        prompter_threads = root / PROMPTER_DIRNAME / THREADS_DIRNAME

        if prompter_threads.exists():

            return prompter_threads

        legacy = root / LEGACY_THREADS_DIRNAME

        if legacy.exists():

            return legacy

        return prompter_threads



    def init_project(

        self,

        name: str,

        *,

        project_id: str | None = None,

        instructions: str | None = None,

        config: dict[str, Any] | None = None,

    ) -> ProjectDetail:

        """Scaffold ``projects/{slug}/`` with template files (filesystem-first UX)."""

        pid = project_id or slugify(name)

        root = self._project_dir(pid)

        if root.exists():

            raise FileExistsError(f"Project already exists: {pid}")



        ensure_dir(root / DOCS_DIRNAME)

        prompter = ensure_dir(root / PROMPTER_DIRNAME)

        ensure_dir(prompter / THREADS_DIRNAME)



        self._instructions_path(pid).write_text(

            instructions if instructions is not None else INSTRUCTIONS_TEMPLATE,

            encoding="utf-8",

        )

        metadata = {

            "id": pid,

            "name": name,

            "created_at": utc_now().isoformat(),

            "config": config or {},

        }

        self._project_yaml_path(pid).write_text(

            yaml.safe_dump(metadata, sort_keys=False),

            encoding="utf-8",

        )

        ChunkStore(root, self.settings).save_chunks([])

        return self.get_project(pid)



    def create_project(

        self,

        name: str,

        system_prompt: str = "",

        *,

        project_id: str | None = None,

        config: dict[str, Any] | None = None,

    ) -> ProjectDetail:

        """Create a project (API/legacy); writes ``instructions.md`` for the system prompt."""

        instructions = system_prompt or None

        return self.init_project(

            name,

            project_id=project_id,

            instructions=instructions,

            config=config,

        )



    def read_system_prompt(self, project_id: str) -> str:

        path = self._instructions_path(project_id)

        if path.is_file():

            return read_text_file(path).strip()

        meta = self._load_metadata(project_id)

        return str(meta.get("system_prompt", "")).strip()



    def sync_docs(self, project_id: str) -> list[dict[str, Any]]:

        root = self._require_project(project_id)

        self._maybe_migrate_layout(project_id)

        return ChunkStore(root, self.settings).sync_docs_dir()



    def resolve_project_ref(self, ref: str) -> str:
        """Resolve a CLI or human reference to a canonical project folder id."""
        ref = ref.strip()
        if not ref:
            raise ProjectNotFoundError("empty project reference")

        exact = self._root / ref
        ids = self._iter_project_ids()
        if exact.is_dir() and self._is_project_dir(exact):
            ref_lower = ref.lower()
            for pid in ids:
                if pid.lower() == ref_lower:
                    return pid
            return ref

        slug = slugify(ref)
        if slug in ids:
            return slug

        ref_lower = ref.lower()
        folder_matches = [pid for pid in ids if pid.lower() == ref_lower]
        if len(folder_matches) == 1:
            return folder_matches[0]
        if len(folder_matches) > 1:
            raise ProjectNotFoundError(
                f"ambiguous project reference {ref!r}: {', '.join(folder_matches)}"
            )

        name_matches: list[str] = []
        for pid in ids:
            try:
                meta = self._load_metadata(pid)
            except ProjectNotFoundError:
                continue
            if str(meta.get("name", "")).lower() == ref_lower:
                name_matches.append(pid)
        if len(name_matches) == 1:
            return name_matches[0]
        if len(name_matches) > 1:
            raise ProjectNotFoundError(
                f"ambiguous project reference {ref!r}: {', '.join(name_matches)}"
            )

        suggestions = self._suggest_project_refs(ref, ids)
        msg = f"project not found: {ref!r}"
        if suggestions:
            msg += f". Did you mean: {', '.join(suggestions)}?"
        raise ProjectNotFoundError(msg)

    def _iter_project_ids(self) -> list[str]:
        if not self._root.exists():
            return []
        return [
            path.name
            for path in sorted(self._root.iterdir())
            if path.is_dir()
            and not path.name.startswith(".")
            and self._is_project_dir(path)
        ]

    def _suggest_project_refs(self, ref: str, ids: list[str]) -> list[str]:
        labels: dict[str, str] = {}
        for pid in ids:
            labels[pid] = pid
            try:
                meta = self._load_metadata(pid)
            except ProjectNotFoundError:
                continue
            name = str(meta.get("name", ""))
            if name:
                labels[name] = pid
        matches = difflib.get_close_matches(ref, list(labels.keys()), n=5, cutoff=0.4)
        seen: set[str] = set()
        result: list[str] = []
        for label in matches:
            pid = labels[label]
            if pid not in seen:
                seen.add(pid)
                result.append(pid)
        return result

    def list_projects(self) -> list[ProjectSummary]:

        summaries: list[ProjectSummary] = []

        if not self._root.exists():

            return summaries

        for path in sorted(self._root.iterdir()):

            if not path.is_dir() or path.name.startswith("."):

                continue

            if not self._is_project_dir(path):

                continue

            try:

                meta = self._load_metadata(path.name)

                summaries.append(self._to_summary(meta))

            except ProjectNotFoundError:

                continue

        return summaries



    def get_project(self, project_id: str) -> ProjectDetail:

        self._require_project(project_id)

        self._maybe_migrate_layout(project_id)

        meta = self._load_metadata(project_id)

        root = self._project_dir(project_id)

        docs_dir = ChunkStore(root, self.settings).docs_dir

        file_count = sum(

            1 for p in docs_dir.glob("*") if p.is_file() and p.suffix.lower() in {".txt", ".md", ".pdf"}

        )

        threads = self.list_threads(project_id)

        instructions_path = self._instructions_path(project_id)

        return ProjectDetail(

            id=meta["id"],

            name=meta["name"],

            created_at=_parse_dt(meta["created_at"]),

            file_count=file_count,

            thread_count=len(threads),

            system_prompt=self.read_system_prompt(project_id),

            config=meta.get("config", {}),

            docs_path=str(docs_dir.resolve()),

            instructions_path=str(instructions_path.resolve()),

        )



    def update_system_prompt(self, project_id: str, system_prompt: str) -> None:

        self._require_project(project_id)

        self._instructions_path(project_id).write_text(system_prompt, encoding="utf-8")



    def add_file(self, project_id: str, file_path: Path) -> list[dict[str, Any]]:

        root = self._require_project(project_id)

        self._maybe_migrate_layout(project_id)

        store = ChunkStore(root, self.settings)

        chunks = store.ingest_file(file_path)

        return [

            {

                "chunk_id": c.chunk_id,

                "source_file": c.source_file,

                "metadata": c.metadata,

            }

            for c in chunks

        ]



    def create_thread(self, project_id: str, title: str | None = None) -> ThreadSummary:

        self._require_project(project_id)

        self._maybe_migrate_layout(project_id)

        thread_id = slugify(title or f"thread-{utc_now().timestamp()}")

        thread_dir = ensure_dir(self._threads_dir(project_id) / thread_id)

        payload = {

            "id": thread_id,

            "title": title,

            "created_at": utc_now().isoformat(),

            "messages": [],

        }

        (thread_dir / "messages.json").write_text(

            json.dumps(payload, indent=2),

            encoding="utf-8",

        )

        return ThreadSummary(

            id=thread_id,

            title=title,

            created_at=_parse_dt(payload["created_at"]),

            message_count=0,

        )



    def list_threads(self, project_id: str) -> list[ThreadSummary]:

        threads_root = self._threads_dir(project_id)

        if not threads_root.exists():

            return []

        result: list[ThreadSummary] = []

        for thread_dir in sorted(threads_root.iterdir()):

            if not thread_dir.is_dir():

                continue

            data = self._load_thread_file(project_id, thread_dir.name)

            messages = data.get("messages", [])

            result.append(

                ThreadSummary(

                    id=data["id"],

                    title=data.get("title"),

                    created_at=_parse_dt(data["created_at"]),

                    message_count=len(messages),

                )

            )

        return result

    def delete_thread(self, project_id: str, thread_id: str) -> None:
        self._require_project(project_id)
        self._maybe_migrate_layout(project_id)
        thread_dir = self._threads_dir(project_id) / thread_id
        if not thread_dir.is_dir():
            raise ThreadNotFoundError(thread_id)
        shutil.rmtree(thread_dir)

    def clear_thread_messages(self, project_id: str, thread_id: str) -> ThreadSummary:
        data = self._load_thread_file(project_id, thread_id)
        data["messages"] = []
        path = self._thread_messages_path(project_id, thread_id)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return ThreadSummary(
            id=data["id"],
            title=data.get("title"),
            created_at=_parse_dt(data["created_at"]),
            message_count=0,
        )

    def rename_thread(self, project_id: str, thread_id: str, title: str) -> ThreadSummary:
        data = self._load_thread_file(project_id, thread_id)
        data["title"] = title
        path = self._thread_messages_path(project_id, thread_id)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        messages = data.get("messages", [])
        return ThreadSummary(
            id=data["id"],
            title=title,
            created_at=_parse_dt(data["created_at"]),
            message_count=len(messages),
        )

    def get_thread_messages(self, project_id: str, thread_id: str) -> list[dict[str, Any]]:

        data = self._load_thread_file(project_id, thread_id)

        return list(data.get("messages", []))



    def append_message(

        self,

        project_id: str,

        thread_id: str,

        role: str,

        content: str,

        *,

        attachments: list[dict[str, Any]] | None = None,

    ) -> dict[str, Any]:

        path = self._thread_messages_path(project_id, thread_id)

        data = self._load_thread_file(project_id, thread_id)

        record: dict[str, Any] = {

            "role": role,

            "content": content,

            "created_at": utc_now().isoformat(),

        }

        if attachments:

            record["attachments"] = attachments

        data.setdefault("messages", []).append(record)

        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

        return record



    def delete_project(self, project_id: str) -> None:

        root = self._project_dir(project_id)

        if not root.exists():

            raise ProjectNotFoundError(project_id)

        shutil.rmtree(root)

    # ------------------------------------------------------------------
    # Sources management (NotebookLM-style)
    # ------------------------------------------------------------------

    def _sources_json_path(self, project_id: str) -> Path:
        return self._project_dir(project_id) / PROMPTER_DIRNAME / "sources.json"

    def _load_sources_json(self, project_id: str) -> dict:
        path = self._sources_json_path(project_id)
        if not path.exists():
            return {"enabled": [], "default_new_enabled": True}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"enabled": [], "default_new_enabled": True}

    def _save_sources_json(self, project_id: str, data: dict) -> None:
        path = self._sources_json_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_enabled_sources(self, project_id: str) -> list[str]:
        """Return list of enabled filenames.  Empty list means *all* docs."""
        self._require_project(project_id)
        return self._load_sources_json(project_id).get("enabled", [])

    def set_enabled_sources(
        self,
        project_id: str,
        enabled: list[str],
        *,
        default_new_enabled: bool = True,
    ) -> None:
        self._require_project(project_id)
        data = self._load_sources_json(project_id)
        data["enabled"] = list(enabled)
        data["default_new_enabled"] = default_new_enabled
        self._save_sources_json(project_id, data)

    def list_doc_files(self, project_id: str) -> SourcesState:
        """Return all docs with enabled/ingested flags."""
        root = self._require_project(project_id)
        self._maybe_migrate_layout(project_id)
        store = ChunkStore(root, self.settings)
        manifest = store._load_ingest_manifest()
        src_data = self._load_sources_json(project_id)
        enabled_set: set[str] = set(src_data.get("enabled", []))
        default_new_enabled: bool = src_data.get("default_new_enabled", True)
        all_enabled = len(enabled_set) == 0  # empty = all

        files: list[DocFileInfo] = []
        for path in sorted(store.docs_dir.glob("*")):
            if not path.is_file():
                continue
            from app.utils import supported_doc_suffix
            if not supported_doc_suffix(path):
                continue
            stat = path.stat()
            ingested = path.name in manifest
            enabled = all_enabled or path.name in enabled_set
            files.append(DocFileInfo(
                name=path.name,
                size=stat.st_size,
                mtime=stat.st_mtime,
                enabled=enabled,
                ingested=ingested,
            ))
        return SourcesState(files=files, default_new_enabled=default_new_enabled)

    def delete_doc_file(self, project_id: str, filename: str) -> None:
        """Remove a doc file, its chunks, and its entry in sources.json."""
        root = self._require_project(project_id)
        self._maybe_migrate_layout(project_id)
        store = ChunkStore(root, self.settings)

        dest = store.docs_dir / filename
        if dest.exists():
            dest.unlink()

        # Drop chunks for this file
        existing = store.load_chunks()
        filtered = [c for c in existing if c.source_file != filename]
        store.save_chunks(filtered)

        # Update ingest manifest
        manifest = store._load_ingest_manifest()
        manifest.pop(filename, None)
        store._save_ingest_manifest(manifest)

        # Update sources.json
        data = self._load_sources_json(project_id)
        data["enabled"] = [f for f in data.get("enabled", []) if f != filename]
        self._save_sources_json(project_id, data)



    def _is_project_dir(self, path: Path) -> bool:

        return any(

            (path / name).exists()

            for name in (PROJECT_YAML, LEGACY_METADATA_YAML, INSTRUCTIONS_FILE)

        )



    def _maybe_migrate_layout(self, project_id: str) -> None:

        """Upgrade legacy ``data/projects`` layout to filesystem-first structure."""

        root = self._project_dir(project_id)

        if not root.exists():

            return



        legacy_meta = root / LEGACY_METADATA_YAML

        project_yaml = root / PROJECT_YAML

        if legacy_meta.exists() and not project_yaml.exists():

            meta = yaml.safe_load(legacy_meta.read_text(encoding="utf-8")) or {}

            prompt = meta.pop("system_prompt", None)

            project_yaml.write_text(

                yaml.safe_dump(meta, sort_keys=False),

                encoding="utf-8",

            )

            instructions = self._instructions_path(project_id)

            if prompt and not instructions.exists():

                instructions.write_text(str(prompt).strip() + "\n", encoding="utf-8")



        legacy_files = root / "files"

        docs = root / DOCS_DIRNAME

        if legacy_files.is_dir() and not docs.exists():

            legacy_files.rename(docs)

        elif legacy_files.is_dir() and docs.exists():

            for item in legacy_files.iterdir():

                target = docs / item.name

                if not target.exists():

                    shutil.move(str(item), str(target))

            if not any(legacy_files.iterdir()):

                legacy_files.rmdir()



        prompter = root / PROMPTER_DIRNAME

        ensure_dir(prompter)

        legacy_chunks = root / "chunks.json"

        if legacy_chunks.exists() and not (prompter / "chunks.json").exists():

            shutil.move(str(legacy_chunks), str(prompter / "chunks.json"))



        legacy_threads = root / LEGACY_THREADS_DIRNAME

        new_threads = prompter / THREADS_DIRNAME

        if legacy_threads.is_dir() and legacy_threads.resolve() != new_threads.resolve():

            if not new_threads.exists():

                shutil.move(str(legacy_threads), str(new_threads))

            else:

                for item in legacy_threads.iterdir():

                    target = new_threads / item.name

                    if not target.exists():

                        shutil.move(str(item), str(target))

                if not any(legacy_threads.iterdir()):

                    legacy_threads.rmdir()



    def _require_project(self, project_id: str) -> Path:

        root = self._project_dir(project_id)

        if not root.is_dir() or not self._is_project_dir(root):

            raise ProjectNotFoundError(project_id)

        return root



    def _load_metadata(self, project_id: str) -> dict[str, Any]:

        root = self._project_dir(project_id)

        if not root.is_dir():

            raise ProjectNotFoundError(project_id)



        path = self._project_yaml_path(project_id)

        if not path.exists():

            legacy = self._legacy_metadata_path(project_id)

            if legacy.exists():

                meta = yaml.safe_load(legacy.read_text(encoding="utf-8")) or {}

            elif self._instructions_path(project_id).exists():

                meta = {

                    "id": project_id,

                    "name": project_id.replace("-", " ").title(),

                    "created_at": utc_now().isoformat(),

                    "config": {},

                }

            else:

                raise ProjectNotFoundError(project_id)

        else:

            meta = yaml.safe_load(path.read_text(encoding="utf-8")) or {}



        meta.setdefault("id", project_id)

        return meta



    def _thread_messages_path(self, project_id: str, thread_id: str) -> Path:

        path = self._threads_dir(project_id) / thread_id / "messages.json"

        if not path.exists():

            raise ThreadNotFoundError(thread_id)

        return path



    def _load_thread_file(self, project_id: str, thread_id: str) -> dict[str, Any]:

        path = self._thread_messages_path(project_id, thread_id)

        return json.loads(path.read_text(encoding="utf-8"))



    def _to_summary(self, meta: dict[str, Any]) -> ProjectSummary:

        project_id = meta["id"]

        detail = self.get_project(project_id)

        return ProjectSummary(

            id=detail.id,

            name=detail.name,

            created_at=detail.created_at,

            file_count=detail.file_count,

            thread_count=detail.thread_count,

        )


