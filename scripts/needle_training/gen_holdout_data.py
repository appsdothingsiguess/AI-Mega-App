#!/usr/bin/env python3
"""
Generates a FRESH held-out test set for the tool-calling registry, separate
from data.jsonl / gen_training_data.py. This is a copy of that generation
logic with:
  - a different random seed (holdout-specific, not 7)
  - broadened/varied phrasing templates and some new fill values per tool,
    so this isn't just a seed-shuffled duplicate of the original phrasings

Used to measure real generalization after finetuning FunctionGemma-270M on
the FULL 250 examples in data.jsonl (no held-out carve-out from that file).

Usage: gen_holdout_data.py [--out data_holdout_v2.jsonl] [--n 60]
"""
import argparse
import json
import random

TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for current information.",
        "parameters": {"query": {"type": "string", "description": "Search query.", "required": True}},
    },
    {
        "name": "fetch_url",
        "description": "Fetch and read the contents of a URL.",
        "parameters": {"url": {"type": "string", "description": "URL to fetch.", "required": True}},
    },
    {
        "name": "file_read",
        "description": "Read a file's contents within the current project.",
        "parameters": {"path": {"type": "string", "description": "Project-relative file path.", "required": True}},
    },
    {
        "name": "file_grep",
        "description": "Search for a text pattern across files in the current project.",
        "parameters": {
            "pattern": {"type": "string", "description": "Regex pattern to search for.", "required": True},
            "path": {"type": "string", "description": "Project-relative directory to search in.", "required": True},
        },
    },
    {
        "name": "run_code",
        "description": "Execute a short code snippet in a sandbox and return its output.",
        "parameters": {
            "language": {"type": "string", "description": "Programming language, e.g. python or javascript.", "required": True},
            "code": {"type": "string", "description": "Code to execute.", "required": True},
        },
    },
    {
        "name": "memory_save",
        "description": "Save a fact or preference to long-term memory for future chats.",
        "parameters": {"content": {"type": "string", "description": "The fact or note to remember.", "required": True}},
    },
]
TOOLS_JSON = json.dumps(TOOLS, separators=(",", ":"))

# --- web_search: original topics + new ones, original templates + new phrasings ---
SEARCH_TOPICS = [
    "the current price of Bitcoin", "who won the last F1 race", "the weather forecast for Tokyo this week",
    "the release date of the next iPhone", "the latest news on the Fed interest rate decision",
    "the population of Vietnam", "what movies are out in theaters this month", "the best restaurants in Lisbon",
    "the current exchange rate between USD and EUR", "recent breakthroughs in fusion energy",
    "the winner of the 2026 Super Bowl", "the latest version of Python", "who the mayor of Chicago is",
    "the opening hours of the Louvre museum", "flight prices from NYC to London next week",
    # new topics
    "the current stock price of Nvidia", "the latest earthquake near San Francisco",
    "how many people live in Seoul", "the score of tonight's Lakers game",
    "the newest release of Node.js", "top-rated coffee shops in Seattle",
    "the current inflation rate in the US", "who directed the latest Dune movie",
]
SEARCH_TEMPLATES = [
    "search the web for {t}",
    "can you look up {t}?",
    "find out {t}",
    "google {t}",
    "what's {t}?",
    "I need to know {t}, can you check online?",
    "look this up: {t}",
    # new templates
    "do a quick web search on {t}",
    "hey, could you dig up {t}?",
    "browse the internet to find {t}",
    "I'm curious about {t} -- search for it",
    "check the web and tell me {t}",
]

# --- fetch_url: original + new URLs and templates ---
URLS = [
    "https://news.ycombinator.com", "https://arxiv.org/abs/2410.01234", "https://github.com/torvalds/linux",
    "https://en.wikipedia.org/wiki/Transformer_(deep_learning)", "https://blog.openai.com",
    "https://www.anthropic.com/news", "https://docs.python.org/3/library/asyncio.html",
    "https://example.com/pricing", "https://status.cloudflare.com", "https://reactjs.org/docs",
    # new URLs
    "https://developer.mozilla.org/en-US/docs/Web/JavaScript", "https://www.rust-lang.org/learn",
    "https://huggingface.co/models", "https://stackoverflow.com/questions/tagged/python",
]
FETCH_TEMPLATES = [
    "fetch {u} and summarize it",
    "what does the page at {u} say?",
    "pull up {u}",
    "go read {u}",
    "can you get the content of {u}?",
    "open {u} and tell me what's on it",
    # new templates
    "load {u} and give me the gist",
    "download the page {u} and summarize",
    "visit {u} for me and report back",
    "check out {u} -- what's there?",
]

# --- file_read: original + new paths and templates ---
FILE_PATHS = [
    "app/main.py", "src/index.ts", "docs/PLAN.md", "app/tools/registry.py", "requirements.txt",
    "README.md", "app/db/models.py", "config.yaml", "app/routes/chat.py", "tests/test_router.py",
    "package.json", "app/tools/impl/web_search.py",
    # new paths
    "app/services/auth.py", "src/utils/logger.ts", "docs/ARCHITECTURE.md", ".env.example",
    "app/tools/impl/memory_save.py", "Dockerfile",
]
FILE_READ_TEMPLATES = [
    "show me what's in {p}",
    "read {p}",
    "open {p} for me",
    "what does {p} contain?",
    "cat {p}",
    "can I see the contents of {p}?",
    # new templates
    "pull up the contents of {p}",
    "let me see {p}",
    "print out {p}",
    "dump the contents of {p} please",
]

# --- file_grep: original + new patterns/dirs/templates ---
GREP_PATTERNS = ["TODO", "FIXME", "def handle_", "import os", "class Router", "api_key", "async def",
                  "raise Exception", "console.log", "SELECT \\* FROM",
                  # new patterns
                  "XXX", "def __init__", "try:", "import re"]
GREP_DIRS = ["app/", "src/", "tests/", "app/tools/", "docs/", ".",
             # new dirs
             "app/services/", "scripts/"]
GREP_TEMPLATES = [
    "search for '{pat}' in {d}",
    "grep '{pat}' inside {d}",
    "find every occurrence of {pat} under {d}",
    "where does {pat} show up in {d}?",
    "look for {pat} in the {d} folder",
    # new templates
    "can you scan {d} for '{pat}'?",
    "find all matches of {pat} in {d}",
    "hunt down {pat} across {d}",
]

# --- run_code: original + new snippets and templates ---
CODE_SNIPPETS = [
    ("python", "print(sum(range(100)))"),
    ("python", "import math; print(math.sqrt(2))"),
    ("javascript", "console.log([1,2,3].map(x => x * 2))"),
    ("python", "print(sorted(['banana','apple','cherry']))"),
    ("python", "print(2**10)"),
    ("javascript", "console.log(new Date().toISOString())"),
    ("python", "print(len('hello world'.split()))"),
    ("python", "print([x for x in range(20) if x % 3 == 0])"),
    # new snippets
    ("python", "print(max([3, 1, 4, 1, 5, 9]))"),
    ("javascript", "console.log(Array.from({length: 5}, (_, i) => i * i))"),
    ("python", "import json; print(json.dumps({'a': 1}))"),
    ("python", "print('hello'[::-1])"),
]
CODE_TEMPLATES = [
    "run this {lang} code: {code}",
    "execute: {code}",
    "can you run `{code}` in {lang}?",
    "test this snippet: {code}",
    "what does this print? {code}",
    # new templates
    "please execute the following {lang}: {code}",
    "try running: {code}",
    "run and show the output of `{code}`",
]

# --- memory_save: original + new facts/templates ---
MEMORY_FACTS = [
    "I prefer dark mode in every app", "my timezone is EST", "I'm allergic to peanuts",
    "always write commit messages in present tense", "my preferred language for scripting is Python",
    "I work best with concise answers, no fluff", "remember that my project uses PostgreSQL not MySQL",
    "I like tabs, not spaces", "my default branch name should be main", "I'm vegetarian",
    "remember I'm on a Linux box, not Mac", "note that I prefer pytest over unittest",
    # new facts
    "I always want responses in markdown", "my company uses AWS not GCP",
    "I go by JD, not my full name", "remember I'm in the Pacific time zone",
    "note that I prefer 2-space indentation", "my favorite editor is Vim",
]
MEMORY_TEMPLATES = [
    "remember that {f}",
    "please save this: {f}",
    "note for future chats: {f}",
    "keep in mind that {f}",
    "{f} -- remember this",
    "save to memory: {f}",
    # new templates
    "store this fact: {f}",
    "jot this down for later: {f}",
    "make a note that {f}",
]


def build_examples(n, seed=20260722):
    buckets = [
        ("web_search", SEARCH_TEMPLATES, SEARCH_TOPICS, lambda t: {"query": t}),
        ("fetch_url", FETCH_TEMPLATES, URLS, lambda u: {"url": u}),
        ("file_read", FILE_READ_TEMPLATES, FILE_PATHS, lambda p: {"path": p}),
        ("memory_save", MEMORY_TEMPLATES, MEMORY_FACTS, lambda f: {"content": f}),
    ]
    examples = []
    rng = random.Random(seed)  # holdout-specific seed, distinct from training seed 7

    per_bucket = n // 6
    for name, templates, fills, argfn in buckets:
        for _ in range(per_bucket):
            tmpl = rng.choice(templates)
            fill = rng.choice(fills)
            key = "{t}" if "{t}" in tmpl else "{u}" if "{u}" in tmpl else "{p}" if "{p}" in tmpl else "{f}"
            query = tmpl.format(**{key.strip("{}"): fill})
            answer = [{"name": name, "arguments": argfn(fill)}]
            examples.append({"query": query, "tools": TOOLS_JSON, "answers": json.dumps(answer, separators=(",", ":"))})

    # file_grep needs two slots
    for _ in range(per_bucket):
        tmpl = rng.choice(GREP_TEMPLATES)
        pat = rng.choice(GREP_PATTERNS)
        d = rng.choice(GREP_DIRS)
        query = tmpl.format(pat=pat, d=d)
        answer = [{"name": "file_grep", "arguments": {"pattern": pat, "path": d}}]
        examples.append({"query": query, "tools": TOOLS_JSON, "answers": json.dumps(answer, separators=(",", ":"))})

    # run_code needs two slots
    for _ in range(n - len(examples)):
        tmpl = rng.choice(CODE_TEMPLATES)
        lang, code = rng.choice(CODE_SNIPPETS)
        query = tmpl.format(lang=lang, code=code)
        answer = [{"name": "run_code", "arguments": {"language": lang, "code": code}}]
        examples.append({"query": query, "tools": TOOLS_JSON, "answers": json.dumps(answer, separators=(",", ":"))})

    rng.shuffle(examples)
    return examples


def dedupe_against(examples, existing_queries):
    """Remove any example whose query exact-matches an existing (training) query."""
    kept, collisions = [], []
    seen = set()
    for ex in examples:
        q = ex["query"]
        if q in existing_queries or q in seen:
            collisions.append(q)
            continue
        seen.add(q)
        kept.append(ex)
    return kept, collisions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data_holdout_v2.jsonl")
    ap.add_argument("--existing", default="data.jsonl", help="Existing training file to dedupe against.")
    ap.add_argument("--n", type=int, default=60)
    args = ap.parse_args()

    # Load existing training queries to dedupe against
    existing_queries = set()
    try:
        with open(args.existing) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                existing_queries.add(json.loads(line)["query"])
    except FileNotFoundError:
        print(f"Warning: {args.existing} not found, skipping dedupe against it.")

    all_collisions = []
    examples = []
    seen_queries = set()
    seed = 20260722
    attempts = 0
    while len(examples) < args.n and attempts < 20:
        batch = build_examples(args.n, seed=seed)
        kept, collisions = dedupe_against(batch, existing_queries | seen_queries)
        all_collisions.extend(collisions)
        for ex in kept:
            if len(examples) >= args.n:
                break
            examples.append(ex)
            seen_queries.add(ex["query"])
        seed += 1
        attempts += 1

    with open(args.out, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    from collections import Counter
    counts = Counter(json.loads(ex["answers"])[0]["name"] for ex in examples)
    print(f"Wrote {len(examples)} examples to {args.out}")
    print("Per-tool counts:", dict(counts))
    if all_collisions:
        print(f"Collisions found & removed during generation ({len(all_collisions)}):")
        for c in all_collisions:
            print("  -", c)
    else:
        print("No collisions with existing training queries found.")


if __name__ == "__main__":
    main()
