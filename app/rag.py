"""Local RAG: chunking, persisted chunks, lexical retrieval (TF-IDF-style)."""



from __future__ import annotations



import json

import math

import re

from collections import Counter

from dataclasses import dataclass

from pathlib import Path

from typing import Any



from pypdf import PdfReader



from app.config import Settings

from app.utils import read_text_file, supported_doc_suffix





TOKEN_RE = re.compile(r"[a-zA-Z0-9_]{2,}")



PROMPTER_DIRNAME = ".prompter"

CHUNKS_FILENAME = "chunks.json"

INGEST_MANIFEST_FILENAME = "ingest.json"

DOCS_DIRNAME = "docs"

LEGACY_FILES_DIRNAME = "files"

LEGACY_CHUNKS_FILENAME = "chunks.json"





@dataclass

class ChunkRecord:

    chunk_id: str

    source_file: str

    text: str

    metadata: dict[str, Any]





class ChunkStore:

    """Persist and search document chunks for a project."""



    def __init__(self, project_dir: Path, settings: Settings) -> None:

        self.project_dir = project_dir

        self.settings = settings

        self.docs_dir = self._resolve_docs_dir()

        self.docs_dir.mkdir(parents=True, exist_ok=True)

        self.prompter_dir = self._resolve_prompter_dir()

        self.prompter_dir.mkdir(parents=True, exist_ok=True)

        self.chunks_path = self.prompter_dir / CHUNKS_FILENAME

        self.ingest_manifest_path = self.prompter_dir / INGEST_MANIFEST_FILENAME



    def _resolve_docs_dir(self) -> Path:

        docs = self.project_dir / DOCS_DIRNAME

        legacy = self.project_dir / LEGACY_FILES_DIRNAME

        if docs.exists():

            return docs

        if legacy.exists():

            return legacy

        return docs



    def _resolve_prompter_dir(self) -> Path:

        prompter = self.project_dir / PROMPTER_DIRNAME

        if prompter.exists():

            return prompter

        legacy_chunks = self.project_dir / LEGACY_CHUNKS_FILENAME

        if legacy_chunks.exists():

            prompter.mkdir(parents=True, exist_ok=True)

            if not (prompter / CHUNKS_FILENAME).exists():

                (prompter / CHUNKS_FILENAME).write_bytes(legacy_chunks.read_bytes())

            return prompter

        return prompter



    def load_chunks(self) -> list[ChunkRecord]:

        path = self.chunks_path

        if not path.exists():

            legacy = self.project_dir / LEGACY_CHUNKS_FILENAME

            if legacy.exists():

                path = legacy

            else:

                return []

        raw = json.loads(path.read_text(encoding="utf-8"))

        return [ChunkRecord(**item) for item in raw]



    def save_chunks(self, chunks: list[ChunkRecord]) -> None:

        payload = [

            {

                "chunk_id": c.chunk_id,

                "source_file": c.source_file,

                "text": c.text,

                "metadata": c.metadata,

            }

            for c in chunks

        ]

        self.chunks_path.write_text(

            json.dumps(payload, indent=2, ensure_ascii=False),

            encoding="utf-8",

        )



    def _load_ingest_manifest(self) -> dict[str, dict[str, Any]]:

        if not self.ingest_manifest_path.exists():

            return {}

        raw = json.loads(self.ingest_manifest_path.read_text(encoding="utf-8"))

        return dict(raw) if isinstance(raw, dict) else {}



    def _save_ingest_manifest(self, manifest: dict[str, dict[str, Any]]) -> None:

        self.ingest_manifest_path.write_text(

            json.dumps(manifest, indent=2),

            encoding="utf-8",

        )



    @staticmethod

    def _file_signature(path: Path) -> dict[str, Any]:

        stat = path.stat()

        return {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}



    def _needs_reingest(self, path: Path, manifest: dict[str, dict[str, Any]]) -> bool:

        name = path.name

        sig = self._file_signature(path)

        prev = manifest.get(name)

        return prev != sig



    def ingest_file(

        self,

        source_path: Path,

        *,

        copy_to_docs: bool = True,

    ) -> list[ChunkRecord]:

        if not supported_doc_suffix(source_path):

            raise ValueError(f"Unsupported file type: {source_path.suffix}")



        dest = source_path

        if copy_to_docs and source_path.resolve().parent != self.docs_dir.resolve():

            dest = self.docs_dir / source_path.name

            if source_path.resolve() != dest.resolve():

                dest.write_bytes(source_path.read_bytes())



        text = extract_text(dest)

        new_chunks = chunk_text(

            text,

            source_file=dest.name,

            chunk_size=self.settings.chunk_size,

            chunk_overlap=self.settings.chunk_overlap,

        )



        existing = self.load_chunks()

        existing = [c for c in existing if c.source_file != dest.name]

        merged = existing + new_chunks

        self.save_chunks(merged)



        manifest = self._load_ingest_manifest()

        manifest[dest.name] = self._file_signature(dest)

        self._save_ingest_manifest(manifest)

        return new_chunks



    def sync_docs_dir(self) -> list[dict[str, Any]]:

        """Ingest new or changed files under ``docs/``; drop chunks for removed files."""

        manifest = self._load_ingest_manifest()

        present_names: set[str] = set()

        ingested: list[dict[str, Any]] = []



        for path in sorted(self.docs_dir.glob("*")):

            if not path.is_file() or not supported_doc_suffix(path):

                continue

            present_names.add(path.name)

            if not self._needs_reingest(path, manifest):

                continue

            chunks = self.ingest_file(path, copy_to_docs=False)

            ingested.extend(

                {

                    "chunk_id": c.chunk_id,

                    "source_file": c.source_file,

                    "metadata": c.metadata,

                }

                for c in chunks

            )



        removed = set(manifest.keys()) - present_names

        if removed:

            existing = self.load_chunks()

            existing = [c for c in existing if c.source_file not in removed]

            self.save_chunks(existing)

            for name in removed:

                manifest.pop(name, None)

            self._save_ingest_manifest(manifest)



        return ingested



    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
        source_files: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Score chunks and return top-k results.

        If *source_files* is given (non-empty set), only chunks whose
        ``source_file`` is in the set are considered.  Pass ``None`` (or omit)
        to search all chunks.
        """
        chunks = self.load_chunks()

        if not chunks:

            return []

        if source_files:
            chunks = [c for c in chunks if c.source_file in source_files]
            if not chunks:
                return []

        k = top_k or self.settings.rag_top_k

        scores = _score_chunks(query, chunks)

        ranked = sorted(scores, key=lambda x: x[0], reverse=True)[:k]

        results: list[dict[str, Any]] = []

        for score, chunk in ranked:

            if score <= 0:

                continue

            results.append(

                {

                    "chunk_id": chunk.chunk_id,

                    "source_file": chunk.source_file,

                    "text": chunk.text,

                    "score": round(score, 4),

                    "metadata": chunk.metadata,

                }

            )

        return results





def extract_text(path: Path) -> str:

    suffix = path.suffix.lower()

    if suffix in {".txt", ".md"}:

        return read_text_file(path)

    if suffix == ".pdf":

        reader = PdfReader(str(path))

        pages = [page.extract_text() or "" for page in reader.pages]

        return "\n\n".join(pages)

    raise ValueError(f"Unsupported file: {path}")





def chunk_text(

    text: str,

    *,

    source_file: str,

    chunk_size: int,

    chunk_overlap: int,

) -> list[ChunkRecord]:

    normalized = re.sub(r"\s+", " ", text).strip()

    if not normalized:

        return []



    chunks: list[ChunkRecord] = []

    start = 0

    index = 0

    while start < len(normalized):

        end = min(len(normalized), start + chunk_size)

        piece = normalized[start:end].strip()

        if piece:

            chunks.append(

                ChunkRecord(

                    chunk_id=f"{source_file}::{index}",

                    source_file=source_file,

                    text=piece,

                    metadata={"start": start, "end": end, "index": index},

                )

            )

            index += 1

        if end >= len(normalized):

            break

        start = max(0, end - chunk_overlap)

    return chunks





def _tokenize(text: str) -> list[str]:

    return [t.lower() for t in TOKEN_RE.findall(text)]





def _score_chunks(query: str, chunks: list[ChunkRecord]) -> list[tuple[float, ChunkRecord]]:

    query_tokens = _tokenize(query)

    if not query_tokens:

        return [(0.0, c) for c in chunks]



    query_counts = Counter(query_tokens)

    doc_freq: Counter[str] = Counter()

    chunk_tokens: list[list[str]] = []



    for chunk in chunks:

        tokens = _tokenize(chunk.text)

        chunk_tokens.append(tokens)

        for term in set(tokens):

            doc_freq[term] += 1



    n_docs = max(len(chunks), 1)

    scores: list[tuple[float, ChunkRecord]] = []



    for chunk, tokens in zip(chunks, chunk_tokens):

        if not tokens:

            scores.append((0.0, chunk))

            continue

        tf = Counter(tokens)

        length_norm = 1.0 + math.log(1 + len(tokens))

        score = 0.0

        for term, q_weight in query_counts.items():

            if term not in tf:

                continue

            idf = math.log((n_docs + 1) / (doc_freq[term] + 1)) + 1.0

            score += (tf[term] / length_norm) * idf * q_weight

        scores.append((score, chunk))



    return scores


