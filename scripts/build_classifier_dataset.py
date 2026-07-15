#!/usr/bin/env python3
"""Build classifier eval gold set: dataset.jsonl + smoke_ids.json.

Every row is filled from eval/classifier/gold_map.json (intent → tools/model/tier).
Re-run from repo root: python scripts/build_classifier_dataset.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "eval" / "classifier"
GOLD_PATH = OUT_DIR / "gold_map.json"
DATASET_PATH = OUT_DIR / "dataset.jsonl"
SMOKE_PATH = OUT_DIR / "smoke_ids.json"

# Curated prompts: ≥20 / intent. notes mark boundary / wrong-tier stress cases.
# source: hand | hf:<dataset> (HF remaps documented in README; this generator is hand-only).
PROMPTS: dict[str, list[tuple[str, str]]] = {
    "general_chat": [
        ("Explain recursion like I'm five.", "concept explain"),
        ("Draft a polite email declining a meeting.", "draft email"),
        ("What are the tradeoffs of REST vs GraphQL?", "advice"),
        ("Summarize the main ideas in README.md for me.", "summarize named file meaning"),
        ("Explain what this config.yaml snippet means.", "explain named file"),
        ("Help me rewrite this paragraph to sound more professional.", "writing help"),
        ("What's a good way to prepare for a system design interview?", "advice"),
        ("Compare SQLite and Postgres for a side project.", "comparison"),
        ("Give me a short toast for a wedding rehearsal dinner.", "creative"),
        ("Explain CAP theorem in plain English.", "concept"),
        ("How should I prioritize backlog items in a solo project?", "advice"),
        ("Translate this sentence to Spanish: The meeting is tomorrow.", "translate"),
        ("Brainstorm names for a note-taking app.", "brainstorm"),
        ("What does HTTP 429 mean?", "concept"),
        ("Critique this elevator pitch and tighten it.", "writing"),
        ("Explain the difference between TCP and UDP.", "concept"),
        ("Help me outline a blog post about remote work.", "outline"),
        ("What questions should I ask in a first 1:1 with a new manager?", "advice"),
        ("Rewrite this commit message to conventional commits style.", "writing"),
        ("Is it better to learn Rust or Go first for systems work?", "advice"),
        ("Explain idempotency with an everyday analogy.", "concept"),
        ("Summarize the pros of using a monorepo.", "advice"),
        ("Open README.md and explain the install steps in your own words.", "boundary: summarize file ≠ file_ops"),
    ],
    "web_search": [
        ("What's the weather today?", "live fact"),
        ("Look up the CEO of OpenAI.", "who is"),
        ("Find the score of last night's Lakers game.", "sports"),
        ("Search online for current mortgage rates in Utah.", "look up"),
        ("Who is the Prime Minister of Canada right now?", "current fact"),
        ("What movies are playing near me this weekend?", "showtimes"),
        ("Find restaurants open late in downtown Denver.", "restaurants"),
        ("Look up the latest iPhone release date.", "product news"),
        ("What's Bitcoin's price today?", "prices"),
        ("Search for today's top tech headlines.", "news"),
        ("Find out what the CDC currently recommends for flu shots.", "guidelines"),
        ("Who won the Super Bowl last year?", "sports fact"),
        ("Look up how to fix this Python error online: UnicodeDecodeError.", "boundary: lookup ≠ coding_advanced"),
        ("What's the current exchange rate USD to EUR?", "prices"),
        ("Find the address and hours for the Salt Lake City DMV.", "local fact"),
        ("Search online for CUDA 12.4 release notes.", "docs lookup"),
        ("What is Apple's current market cap?", "live fact"),
        ("Find showtimes for Dune Part Two tonight.", "showtimes"),
        ("Look up the weather forecast for Seattle this week.", "weather"),
        ("Who is the current CTO at Microsoft?", "who is"),
        ("Search for recent news about the James Webb telescope.", "news"),
        ("What's the population of Austin, Texas right now roughly?", "stat lookup"),
        ("Find out whether Costco is open on Memorial Day.", "hours"),
    ],
    "deep_research": [
        ("Summarize recent papers on protein folding.", "papers"),
        ("Literature review on federated learning privacy.", "lit review"),
        ("Compile findings on RAG evaluation benchmarks across sources.", "compile"),
        ("Write a research brief synthesizing studies on LLM hallucination rates.", "research brief"),
        ("Analyze trends in EV battery chemistry from academic and industry reports.", "trends"),
        ("Synthesize the latest research on transformer efficiency techniques.", "synthesis"),
        ("Produce a multi-source overview of differential privacy in ML.", "multi-source"),
        ("Survey academic literature on neural architecture search.", "survey"),
        ("Research brief: compare retrieval methods for long-context QA.", "compare sources"),
        ("Compile a findings memo on CRISPR off-target effects from papers.", "memo"),
        ("Summarize studies comparing vector DBs for hybrid search.", "studies"),
        ("Literature review of prompt injection defenses 2023–2025.", "lit review"),
        ("Analyze climate adaptation strategies across IPCC and peer-reviewed sources.", "multi-source"),
        ("Synthesize research on small language model distillation.", "synthesis"),
        ("Compile evidence on async vs sync agent frameworks from papers and blogs.", "compile"),
        ("Research brief on post-quantum crypto migration for web apps.", "research brief"),
        ("Survey literature on multimodal grounding failures.", "survey"),
        ("Multi-source analysis of remote team productivity studies.", "trends"),
        ("Summarize academic papers on Graph Neural Networks for recommendation.", "papers"),
        ("Literature review: evaluation metrics for code generation models.", "lit review"),
        ("Compile findings on time-series foundation models from recent papers.", "compile"),
        ("Synthesize studies on spacing effects in learning science.", "synthesis"),
        ("Deep multi-source research report on AI watermarking techniques.", "boundary: research ≠ pdf_gen unless PDF asked"),
    ],
    "coding_basic": [
        ("Write a Python function that validates emails with a regex.", "new utility"),
        ("Implement OAuth login scaffolding for a FastAPI app.", "scaffold"),
        ("Add a Redis caching layer helper module.", "new code"),
        ("Create a TypeScript hook that debounces search input.", "new hook"),
        ("Write a SQL migration to add a users.email_verified column.", "migration"),
        ("Scaffold a React component for a settings toggle.", "scaffold"),
        ("Implement a small CLI that converts CSV to JSON.", "new script"),
        ("Write a regex to match US phone numbers.", "regex"),
        ("Create a Pydantic model for a chat message payload.", "schema"),
        ("Add an isolated FastAPI health-check endpoint.", "new endpoint"),
        ("Write a bash-friendly Python script that renames files by date.", "new script"),
        ("Implement a Fibonacci generator in Go.", "new code"),
        ("Create a JSON Schema for a product catalog entry.", "schema"),
        ("Write a utility to slugify blog titles in JavaScript.", "new utility"),
        ("Scaffold a Dockerfile for a Node Express API.", "scaffold"),
        ("Implement an async retry helper with exponential backoff.", "new utility"),
        ("Write a short Rust program that reads stdin and counts words.", "new code"),
        ("Create a Zod schema for signup form validation.", "schema"),
        ("Add a new Next.js API route that returns server time.", "new endpoint"),
        ("Write a PowerShell script that lists largest files under a folder.", "new script"),
        ("Implement a circular buffer class in Python from scratch.", "new code"),
        ("Create a simple Express middleware that logs request duration.", "new code"),
        ("Write a self-contained Python script to generate UUIDs in bulk.", "boundary: must use coding-light not coding-heavy"),
    ],
    "coding_advanced": [
        ("Fix this bug in my Python code: list index out of range in parse_rows.", "fix existing"),
        ("Debug this Go error: panic: runtime error: invalid memory address.", "debug"),
        ("Add unit tests for this UserService class.", "tests for existing"),
        ("Refactor this tangled React useEffect into smaller hooks.", "refactor"),
        ("Review my PR for N+1 query issues in the ORM usage.", "review"),
        ("Optimize this slow SQL query that joins four large tables.", "optimize"),
        ("Resolve this merge conflict in app/chat_orchestrator.py.", "merge conflict"),
        ("Why is this pytest failing with AssertionError on streaming chunks?", "failing tests"),
        ("Fix the race condition in our session cache invalidation.", "bugfix"),
        ("Debug CI: flake8 fails only on Ubuntu runners for this file.", "CI"),
        ("Refactor the duplicate validation logic across these two modules.", "refactor"),
        ("Review this diff for security issues around path traversal.", "review"),
        ("Fix the memory leak when clients disconnect mid-SSE stream.", "fix"),
        ("My Docker build fails at the pip install step — help diagnose.", "debug build"),
        ("Optimize this TypeScript reduce that's O(n^2) on large lists.", "optimize"),
        ("Add regression tests for the router keyword fallthrough case.", "tests"),
        ("Fix flaky Playwright test that race-conditions on the stop button.", "flaky test"),
        ("Debug Redis connection resets under load in production logs.", "debug"),
        ("Refactor error handling so adapters don't leak LiteLLM exceptions.", "refactor"),
        ("Review stack trace and patch the NullPointer in MessageBubble rendering.", "stack trace"),
        ("CI is red: mypy complains about Awaitable types in the orchestrator.", "CI typing"),
        ("Fix the off-by-one in our pagination cursor implementation.", "bug"),
        ("Debug and fix this ClassCastException in the Java worker pool.", "boundary: coding-heavy not coding-light"),
    ],
    "bash": [
        ("Run git pull origin main.", "git"),
        ("Execute pytest -q in the repo root.", "pytest"),
        ("Start the docker compose stack for Ollama and Qdrant.", "docker"),
        ("Run npm run build in the web directory.", "npm"),
        ("Execute pip install -e . on my machine.", "pip"),
        ("Run python main.py with the current venv.", "run script"),
        ("Test on my PC: run ./scripts/start_prompter.py.", "run script"),
        ("Execute git status && git diff --stat.", "git"),
        ("Start uvicorn for app.main on port 8000.", "start server"),
        ("Run docker ps and show only running containers.", "docker"),
        ("Execute npm ci && npm test in web/.", "npm"),
        ("Run black --check app/ tests/.", "lint"),
        ("Execute make migrate if the Makefile target exists.", "make"),
        ("Run curl against localhost:8000/health.", "shell"),
        ("Start redis-server in the background.", "start service"),
        ("Execute poetry install and then poetry run pytest.", "poetry"),
        ("Run git checkout -b feat/tmp only if clean.", "git"),
        ("Execute ls -la and show the largest files in ./logs.", "shell"),
        ("Run pnpm install then pnpm lint.", "pnpm"),
        ("Execute systemctl status docker (or the Windows equivalent check).", "service"),
        ("Run the smoke script: bash scripts/copy_ollama_model.sh --dry-run.", "bash script"),
        ("Start postgres via docker compose up -d db.", "docker"),
        ("Execute rg -n ClassifierOutput app/ and print matches.", "shell search"),
    ],
    "pdf_gen": [
        ("Convert markdown README to PDF.", "md→pdf"),
        ("Make me a PDF research report on AI safety.", "boundary: PDF deliverable"),
        ("Export this meeting notes document as a PDF.", "export"),
        ("Create a PDF from these bullet points about Q3 goals.", "create pdf"),
        ("Convert the Word doc proposal.docx to PDF.", "docx→pdf"),
        ("Turn these images into a single PDF.", "images→pdf"),
        ("Generate a PDF invoice template with sample line items.", "generate"),
        ("Export the project architecture notes to a printable PDF.", "export"),
        ("Convert presentation slides markdown into a PDF deck.", "slides→pdf"),
        ("Create a one-page PDF resume from this text.", "create"),
        ("Make a PDF checklist for the release process.", "create"),
        ("Convert repo docs/getting-started.md into a PDF handout.", "repo docs→pdf"),
        ("Generate a PDF cover letter from this draft email.", "generate"),
        ("Export the weekly status update as PDF for stakeholders.", "export"),
        ("Create a PDF of these API endpoint tables.", "create"),
        ("Convert HTML report to PDF preserving headings.", "html→pdf"),
        ("Make a PDF flashcard sheet from this glossary.", "create"),
        ("Generate a PDF receipt for order #4412.", "generate"),
        ("Turn the design brief markdown into a client-ready PDF.", "md→pdf"),
        ("Export chat transcript to a PDF archive.", "export"),
        ("Create a PDF one-pager comparing three cloud vendors.", "create"),
        ("Convert these scanned note images into one PDF packet.", "images→pdf"),
        ("Make a PDF research summary handout (PDF is the output format).", "boundary: pdf_gen not deep_research"),
    ],
    "file_ops": [
        ("Find the invoice PDF in my downloads.", "find local"),
        ("Open package.json and show dependencies.", "open file"),
        ("Search my codebase for TODO comments.", "code search"),
        ("List files in the ./logs directory.", "list"),
        ("Copy settings.example.json to settings.json locally.", "copy"),
        ("Move old.csv from downloads into the project data/ folder.", "move"),
        ("Delete the temporary files under .pytest-tmp/run.", "delete"),
        ("Find where RouteSource is defined in this repo.", "locate"),
        ("Open app/config.py and show the classifier prompt section path.", "open"),
        ("Search the project for enabled_tools usages.", "search codebase"),
        ("Locate all *.env.example files under the repo.", "find"),
        ("List the largest folders in my home downloads directory.", "list"),
        ("Find the meeting notes PDF on disk named q2-sync.", "find local pdf"),
        ("Read the contents of pyproject.toml dependencies list.", "read file"),
        ("Copy the logo.png from assets/ into web/public/.", "copy"),
        ("Find every occurrence of ChatService (dead name) in the tree.", "search"),
        ("Open docker-compose.yml and show the Ollama service block.", "open"),
        ("Locate the smoke script under scripts/.", "locate"),
        ("List markdown files in docs/ sorted by modified time.", "list"),
        ("Find invoice-2024.pdf somewhere under my user downloads.", "boundary: disk search ≠ vision"),
        ("Search the codebase for HybridRouter class definition.", "code search"),
        ("Move stale log files older than 30 days out of logs/.", "move"),
        ("Open README.md just to show the file path and first lines (filesystem).", "boundary: file_ops not general_chat summarize"),
    ],
    "vision": [
        ("Classify the document type from this image.", "classify image"),
        ("Read the labels in this chart image.", "OCR chart"),
        ("Describe what's in this screenshot of the settings modal.", "describe"),
        ("OCR the text from this photo of a whiteboard.", "OCR"),
        ("Identify the plant in this photo.", "identify"),
        ("What does this diagram show? (attached image)", "diagram"),
        ("Read the numbers from this screenshot of a dashboard.", "OCR numbers"),
        ("Classify whether this image is a receipt or an invoice.", "classify"),
        ("Describe the UI layout in this attached mockup image.", "describe UI"),
        ("Extract table cells visible in this spreadsheet screenshot.", "OCR table"),
        ("Identify logos present in this attached photo.", "identify"),
        ("Read the error message text in this terminal screenshot.", "OCR"),
        ("What breed is the dog in this photo?", "identify"),
        ("Describe the architecture flow in this diagram image.", "diagram"),
        ("OCR the handwritten notes in this image.", "OCR"),
        ("Classify the chart type in the attached figure (bar/line/pie).", "classify"),
        ("Read street signs visible in this photo.", "OCR"),
        ("Describe differences between these two UI screenshots.", "compare images"),
        ("Extract the title text from this slide image.", "OCR"),
        ("Identify objects on the desk in this attached photo.", "identify"),
        ("Read the axis labels on this plotted figure image.", "OCR chart"),
        ("What is highlighted in red in this screenshot?", "describe"),
        ("OCR this image of a PDF page (vision on attached image, not disk find).", "boundary: vision ≠ file_ops"),
    ],
    "reasoning_medium": [
        ("Think through this logic puzzle step by step.", "puzzle"),
        ("Solve this puzzle about three switches and three bulbs.", "puzzle"),
        ("Plan how to schedule these conflicting constraints for the week.", "multi-step plan"),
        ("Work through this probability word problem carefully.", "math"),
        ("Reason step by step: where should the ambulance go first?", "logic"),
        ("Think through tradeoffs for a 2-week migration of the auth service.", "plan"),
        ("Solve: farmers, wolves, goats, cabbage crossing — list minimal moves.", "puzzle"),
        ("Step through diagnosing why a queue depth grows then collapses daily.", "reasoning"),
        ("Plan a study schedule with three exams and two projects overlapping.", "plan"),
        ("Reason about this lateral-thinking riddle before answering.", "puzzle"),
        ("Work through this combinatorics problem with clear intermediate steps.", "math"),
        ("Think through how to allocate 5 engineers across 3 launch risks.", "plan"),
        ("Solve the truth-teller/liar island puzzle with justification.", "logic"),
        ("Step-by-step: optimize a commute with two bus lines and walking.", "multi-step"),
        ("Reason through this supply vs demand pricing vignette.", "logic"),
        ("Plan a cutover checklist with rollback criteria (moderate complexity).", "plan"),
        ("Think through this Einstein-style grid logic puzzle.", "puzzle"),
        ("Solve the river crossing with a flashlight and timed bridges.", "puzzle"),
        ("Work through Bayesian updating on this disease test vignette.", "math"),
        ("Reason about fair division of chores among four roommates.", "logic"),
        ("Step through this spreadsheet formula dependency cycle mentally.", "reasoning"),
        ("Plan a two-day weekend itinerary under budget and weather constraints.", "plan"),
        ("Think through this hashing collision interview puzzle carefully.", "boundary: reasoning-medium not coding-*"),
    ],
    "reasoning_heavy": [
        ("Do a root cause analysis of this multi-system failure.", "RCA"),
        ("Deep reasoning on a complex multi-step plan with tradeoffs.", "heavy plan"),
        ("Analyze cascading failure modes across API, cache, and DB tiers.", "RCA"),
        ("Design a multi-constraint capacity plan for 10x holiday traffic.", "constraints"),
        ("Root-cause why auth latency spikes only for EU users at 09:00 UTC.", "RCA"),
        ("Plan a zero-downtime multi-region cutover with legal hold constraints.", "heavy plan"),
        ("Deep analysis of competing incentives in this org redesign scenario.", "expert reasoning"),
        ("Reason through nested failure domains in a Kubernetes + Kafka + PG stack.", "RCA"),
        ("Construct an expert mitigation plan for a supply-chain compromise.", "expert"),
        ("Multi-constraint schedule: factory lines, SLA penalties, and staffing rules.", "constraints"),
        ("Deep root-cause: intermittent SSE disconnects only behind one CDN POPs.", "RCA"),
        ("Plan a phased decommission of a monolith with dual-write invariants.", "heavy plan"),
        ("Analyze second-order effects of introducing usage-based pricing.", "expert"),
        ("Reason through game-theoretic auction design for limited GPU inventory.", "expert"),
        ("Root cause a multi-day data drift incident across ML feature pipelines.", "RCA"),
        ("Heavy multi-step plan: merge three product lines without breaking bills.", "heavy plan"),
        ("Deep tradeoff analysis: consistency vs availability for sync-critical booking.", "expert"),
        ("Construct recovery procedures for correlated power+network datacenter loss.", "constraints"),
        ("Analyze why canary metrics look green while customer ops tickets spike.", "RCA"),
        ("Reason through a legal/ops/engineering compliance rollout with hard deadlines.", "constraints"),
        ("Deep plan: migrate encrypted PII stores under auditor live review.", "heavy plan"),
        ("Expert-level RCA of flapping BGP routes causing multi-region brownouts.", "RCA"),
        ("Heavy reasoning: redesign incident command for three parallel SEVs.", "boundary: reasoning-heavy not coding_advanced"),
    ],
}


def load_gold() -> dict:
    with GOLD_PATH.open(encoding="utf-8") as f:
        gold = json.load(f)
    if not isinstance(gold, dict) or not gold:
        raise SystemExit(f"Invalid gold map: {GOLD_PATH}")
    return gold


def build_rows(gold: dict) -> list[dict]:
    rows: list[dict] = []
    for intent, prompts in PROMPTS.items():
        if intent not in gold:
            raise SystemExit(f"Intent {intent!r} missing from gold_map.json")
        g = gold[intent]
        prefix = {
            "general_chat": "gc",
            "web_search": "ws",
            "deep_research": "dr",
            "coding_basic": "cb",
            "coding_advanced": "ca",
            "bash": "ba",
            "pdf_gen": "pd",
            "file_ops": "fo",
            "vision": "vi",
            "reasoning_medium": "rm",
            "reasoning_heavy": "rh",
        }[intent]
        for i, (message, notes) in enumerate(prompts, start=1):
            rows.append(
                {
                    "id": f"{prefix}-{i:03d}",
                    "message": message,
                    "intent": intent,
                    "tools": list(g["tools"]),
                    "model": g["model"],
                    "tier": g["tier"],
                    "notes": notes,
                    "source": "hand",
                }
            )
    return rows


def validate_rows(rows: list[dict], gold: dict) -> None:
    errors: list[str] = []
    seen_ids: set[str] = set()
    counts: dict[str, int] = {k: 0 for k in gold}

    for row in rows:
        rid = row.get("id")
        if not rid or rid in seen_ids:
            errors.append(f"bad/duplicate id: {rid!r}")
        seen_ids.add(rid)

        intent = row.get("intent")
        if intent not in gold:
            errors.append(f"{rid}: unknown intent {intent!r}")
            continue
        counts[intent] = counts.get(intent, 0) + 1
        g = gold[intent]
        if row.get("model") != g["model"]:
            errors.append(f"{rid}: model {row.get('model')!r} != gold {g['model']!r}")
        if row.get("tier") != g["tier"]:
            errors.append(f"{rid}: tier {row.get('tier')!r} != gold {g['tier']!r}")
        if sorted(row.get("tools") or []) != sorted(g["tools"]):
            errors.append(f"{rid}: tools {row.get('tools')!r} != gold {g['tools']!r}")
        for key in ("message", "notes", "source"):
            if key not in row or row[key] in (None, ""):
                errors.append(f"{rid}: missing {key}")

    for intent, n in counts.items():
        if n < 20:
            errors.append(f"intent {intent}: only {n} rows (need ≥20)")

    n = len(rows)
    if n < 200 or n > 300:
        errors.append(f"row count {n} outside 200–300")

    if errors:
        print("VALIDATION FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        raise SystemExit(1)

    print(f"OK: {n} rows; per-intent: {counts}")


def smoke_ids(rows: list[dict], gold: dict) -> dict[str, list[str]]:
    # Deterministic: first, middle, last id per intent (3 each).
    by_intent: dict[str, list[str]] = {k: [] for k in gold}
    for row in rows:
        by_intent[row["intent"]].append(row["id"])
    out: dict[str, list[str]] = {}
    for intent, ids in by_intent.items():
        if len(ids) < 3:
            raise SystemExit(f"Cannot build smoke for {intent}: {len(ids)} ids")
        mid = len(ids) // 2
        picked = [ids[0], ids[mid], ids[-1]]
        out[intent] = picked
    return out


def main() -> None:
    gold = load_gold()
    rows = build_rows(gold)
    validate_rows(rows, gold)
    smoke = smoke_ids(rows, gold)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with DATASET_PATH.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    with SMOKE_PATH.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(smoke, f, indent=2, ensure_ascii=False)
        f.write("\n")

    flat = [i for ids in smoke.values() for i in ids]
    print(f"Wrote {DATASET_PATH} ({len(rows)} rows)")
    print(f"Wrote {SMOKE_PATH} ({len(flat)} smoke ids, 3×{len(smoke)} intents)")


if __name__ == "__main__":
    main()
