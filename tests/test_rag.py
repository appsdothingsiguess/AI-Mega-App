"""Tests for chunking and lexical retrieval."""



from pathlib import Path



from app.config import Settings

from app.rag import PROMPTER_DIRNAME, ChunkStore, chunk_text, extract_text





def test_chunk_text_overlap() -> None:

    text = "word " * 400

    chunks = chunk_text(

        text,

        source_file="long.txt",

        chunk_size=100,

        chunk_overlap=20,

    )

    assert len(chunks) > 1

    assert chunks[0].source_file == "long.txt"





def test_retrieve_relevant_chunk(tmp_path: Path) -> None:

    settings = Settings(projects_dir=tmp_path / "projects", data_dir=tmp_path / "data")

    project_dir = tmp_path / "projects" / "rag-demo"

    (project_dir / "docs").mkdir(parents=True)

    (project_dir / PROMPTER_DIRNAME).mkdir(parents=True)

    store = ChunkStore(project_dir, settings)



    doc = project_dir / "docs" / "facts.md"

    doc.write_text(

        "The capital of France is Paris. The Eiffel Tower is in Paris.",

        encoding="utf-8",

    )

    store.ingest_file(doc, copy_to_docs=False)



    hits = store.retrieve("What is the capital of France?")

    assert hits

    assert "Paris" in hits[0]["text"]





def test_sync_docs_skips_unchanged(tmp_path: Path) -> None:

    settings = Settings(projects_dir=tmp_path / "projects", data_dir=tmp_path / "data")

    project_dir = tmp_path / "projects" / "sync-demo"

    (project_dir / "docs").mkdir(parents=True)

    store = ChunkStore(project_dir, settings)

    doc = project_dir / "docs" / "a.txt"

    doc.write_text("hello world", encoding="utf-8")

    first = store.sync_docs_dir()

    assert len(first) >= 1

    second = store.sync_docs_dir()

    assert second == []





def test_extract_txt(tmp_path: Path) -> None:

    path = tmp_path / "a.txt"

    path.write_text("hello", encoding="utf-8")

    assert extract_text(path) == "hello"


