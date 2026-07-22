#!/usr/bin/env python3
"""
Generates local Needle finetune data from this app's real tool registry
(docs/FEATURES.md F9: web_search, fetch_url, file_ops read/grep, run_code,
memory_save) -- no Gemini API key needed (that's only needle's own
auto-generation path, `needle generate-data`; the finetune CLI just needs a
JSONL file, which this script produces directly).

Each example carries the FULL tool list (so the model learns selection, not
just slot-filling) and one correct answer. Needle's own docs recommend
>=120 examples/tool for a real finetune; at 250 total across 6 tools (~42/tool)
this is a quick/shallow pass, not that -- flagged in the doc it feeds.

Usage: gen_training_data.py [--out data.jsonl] [--n 250]
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

SEARCH_TOPICS = [
    "the current price of Bitcoin", "who won the last F1 race", "the weather forecast for Tokyo this week",
    "the release date of the next iPhone", "the latest news on the Fed interest rate decision",
    "the population of Vietnam", "what movies are out in theaters this month", "the best restaurants in Lisbon",
    "the current exchange rate between USD and EUR", "recent breakthroughs in fusion energy",
    "the winner of the 2026 Super Bowl", "the latest version of Python", "who the mayor of Chicago is",
    "the opening hours of the Louvre museum", "flight prices from NYC to London next week",
]
SEARCH_TEMPLATES = [
    "search the web for {t}",
    "can you look up {t}?",
    "find out {t}",
    "google {t}",
    "what's {t}?",
    "I need to know {t}, can you check online?",
    "look this up: {t}",
]

URLS = [
    "https://news.ycombinator.com", "https://arxiv.org/abs/2410.01234", "https://github.com/torvalds/linux",
    "https://en.wikipedia.org/wiki/Transformer_(deep_learning)", "https://blog.openai.com",
    "https://www.anthropic.com/news", "https://docs.python.org/3/library/asyncio.html",
    "https://example.com/pricing", "https://status.cloudflare.com", "https://reactjs.org/docs",
]
FETCH_TEMPLATES = [
    "fetch {u} and summarize it",
    "what does the page at {u} say?",
    "pull up {u}",
    "go read {u}",
    "can you get the content of {u}?",
    "open {u} and tell me what's on it",
]

FILE_PATHS = [
    "app/main.py", "src/index.ts", "docs/PLAN.md", "app/tools/registry.py", "requirements.txt",
    "README.md", "app/db/models.py", "config.yaml", "app/routes/chat.py", "tests/test_router.py",
    "package.json", "app/tools/impl/web_search.py",
]
FILE_READ_TEMPLATES = [
    "show me what's in {p}",
    "read {p}",
    "open {p} for me",
    "what does {p} contain?",
    "cat {p}",
    "can I see the contents of {p}?",
]

GREP_PATTERNS = ["TODO", "FIXME", "def handle_", "import os", "class Router", "api_key", "async def",
                  "raise Exception", "console.log", "SELECT \\* FROM"]
GREP_DIRS = ["app/", "src/", "tests/", "app/tools/", "docs/", "."]
GREP_TEMPLATES = [
    "search for '{pat}' in {d}",
    "grep '{pat}' inside {d}",
    "find every occurrence of {pat} under {d}",
    "where does {pat} show up in {d}?",
    "look for {pat} in the {d} folder",
]

CODE_SNIPPETS = [
    ("python", "print(sum(range(100)))"),
    ("python", "import math; print(math.sqrt(2))"),
    ("javascript", "console.log([1,2,3].map(x => x * 2))"),
    ("python", "print(sorted(['banana','apple','cherry']))"),
    ("python", "print(2**10)"),
    ("javascript", "console.log(new Date().toISOString())"),
    ("python", "print(len('hello world'.split()))"),
    ("python", "print([x for x in range(20) if x % 3 == 0])"),
]
CODE_TEMPLATES = [
    "run this {lang} code: {code}",
    "execute: {code}",
    "can you run `{code}` in {lang}?",
    "test this snippet: {code}",
    "what does this print? {code}",
]

MEMORY_FACTS = [
    "I prefer dark mode in every app", "my timezone is EST", "I'm allergic to peanuts",
    "always write commit messages in present tense", "my preferred language for scripting is Python",
    "I work best with concise answers, no fluff", "remember that my project uses PostgreSQL not MySQL",
    "I like tabs, not spaces", "my default branch name should be main", "I'm vegetarian",
    "remember I'm on a Linux box, not Mac", "note that I prefer pytest over unittest",
]
MEMORY_TEMPLATES = [
    "remember that {f}",
    "please save this: {f}",
    "note for future chats: {f}",
    "keep in mind that {f}",
    "{f} -- remember this",
    "save to memory: {f}",
]


def build_examples(n):
    buckets = [
        ("web_search", SEARCH_TEMPLATES, SEARCH_TOPICS, lambda t: {"query": t}),
        ("fetch_url", FETCH_TEMPLATES, URLS, lambda u: {"url": u}),
        ("file_read", FILE_READ_TEMPLATES, FILE_PATHS, lambda p: {"path": p}),
        ("memory_save", MEMORY_TEMPLATES, MEMORY_FACTS, lambda f: {"content": f}),
    ]
    examples = []
    rng = random.Random(7)

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data.jsonl")
    ap.add_argument("--n", type=int, default=250)
    args = ap.parse_args()

    examples = build_examples(args.n)
    with open(args.out, "w") as f:
        for ex in examples:
            f.write(json.dumps(ex) + "\n")

    from collections import Counter
    counts = Counter(json.loads(ex["answers"])[0]["name"] for ex in examples)
    print(f"Wrote {len(examples)} examples to {args.out}")
    print("Per-tool counts:", dict(counts))


if __name__ == "__main__":
    main()
