#!/usr/bin/env python3
"""
Phase-0.5 Test 7 item 7: generates a harder held-out tool-calling test set
than data.jsonl's real 6-tool registry, using an EXPANDED 13-tool registry
with deliberately overlapping/confusable names (e.g. file_read vs
file_read_lines vs read_file_metadata; web_search vs web_search_news vs
web_search_images; fetch_url vs fetch_url_raw). Same generation pattern as
gen_holdout_data.py (templated phrasing + fill values, deduped against
data.jsonl), just against the bigger/ambiguous registry.

Usage: gen_stress_data.py [--out data_stress.jsonl] [--n 90]
"""
import argparse
import json
import random
import re

TOOLS = [
    {"name": "web_search", "description": "Search the general web for current information.",
     "parameters": {"query": {"type": "string", "description": "Search query.", "required": True}}},
    {"name": "web_search_news", "description": "Search specifically for recent news articles.",
     "parameters": {"query": {"type": "string", "description": "News search query.", "required": True}}},
    {"name": "web_search_images", "description": "Search specifically for images.",
     "parameters": {"query": {"type": "string", "description": "Image search query.", "required": True}}},
    {"name": "fetch_url", "description": "Fetch and read the rendered/cleaned text contents of a URL.",
     "parameters": {"url": {"type": "string", "description": "URL to fetch.", "required": True}}},
    {"name": "fetch_url_raw", "description": "Fetch the raw unprocessed HTML/response body of a URL.",
     "parameters": {"url": {"type": "string", "description": "URL to fetch raw.", "required": True}}},
    {"name": "file_read", "description": "Read a file's full contents within the current project.",
     "parameters": {"path": {"type": "string", "description": "Project-relative file path.", "required": True}}},
    {"name": "file_read_lines", "description": "Read only a specific line range from a file.",
     "parameters": {
         "path": {"type": "string", "description": "Project-relative file path.", "required": True},
         "start_line": {"type": "integer", "description": "First line to read.", "required": True},
         "end_line": {"type": "integer", "description": "Last line to read.", "required": True},
     }},
    {"name": "read_file_metadata", "description": "Read only a file's metadata (size, mtime, permissions), not its contents.",
     "parameters": {"path": {"type": "string", "description": "Project-relative file path.", "required": True}}},
    {"name": "file_grep", "description": "Search for a text pattern across files in the current project.",
     "parameters": {
         "pattern": {"type": "string", "description": "Regex pattern to search for.", "required": True},
         "path": {"type": "string", "description": "Project-relative directory to search in.", "required": True},
     }},
    {"name": "run_code", "description": "Execute a short code snippet in a sandbox and return its output.",
     "parameters": {
         "language": {"type": "string", "description": "Programming language.", "required": True},
         "code": {"type": "string", "description": "Code to execute.", "required": True},
     }},
    {"name": "memory_save", "description": "Save a new fact or preference to long-term memory.",
     "parameters": {"content": {"type": "string", "description": "The fact or note to remember.", "required": True}}},
    {"name": "memory_search", "description": "Search existing long-term memory for a previously saved fact.",
     "parameters": {"query": {"type": "string", "description": "What to search memory for.", "required": True}}},
    {"name": "memory_delete", "description": "Delete a previously saved memory entry.",
     "parameters": {"content": {"type": "string", "description": "The fact/note text to remove.", "required": True}}},
]
TOOLS_JSON = json.dumps(TOOLS, separators=(",", ":"))

BUCKETS = [
    ("web_search", ["search the web for {t}", "look up {t}", "google {t}", "find out {t} online"],
     ["the current price of gold", "the newest Rust release", "who invented the transformer architecture",
      "the population of Peru", "the best pizza place in Naples"],
     lambda t: {"query": t}),
    ("web_search_news", ["what's in the news about {t}", "find recent news articles on {t}",
                          "search news for {t}", "any breaking news on {t}?"],
     ["the upcoming election", "the tech layoffs this quarter", "the new EU AI regulations",
      "the merger between the two chip companies", "the recent data center outage"],
     lambda t: {"query": t}),
    ("web_search_images", ["find images of {t}", "search for pictures of {t}", "show me images of {t}",
                            "image search: {t}"],
     ["a Kalman filter diagram", "the RTX 3090 GPU", "a sqlite-vec architecture diagram",
      "a llama.cpp server dashboard", "a vector database index"],
     lambda t: {"query": t}),
    ("fetch_url", ["fetch {u} and summarize it", "what does the page at {u} say?", "pull up {u}",
                   "read {u} for me"],
     ["https://arxiv.org/abs/2410.05678", "https://blog.anthropic.com", "https://pytorch.org/docs",
      "https://vitejs.dev/guide", "https://qdrant.tech/documentation"],
     lambda u: {"url": u}),
    ("fetch_url_raw", ["get the raw HTML of {u}", "fetch the unprocessed response body from {u}",
                       "grab the raw page source at {u}", "download the raw contents of {u}, not cleaned up"],
     ["https://example.com/api/status", "https://httpbin.org/get", "https://raw.githubusercontent.com/torvalds/linux/master/README",
      "https://en.wikipedia.org/wiki/HTTP", "https://developer.mozilla.org/en-US/"],
     lambda u: {"url": u}),
    ("file_read", ["show me what's in {p}", "read {p}", "open {p} for me", "cat {p}"],
     ["app/main.py", "docs/PLAN.md", "config.yaml", "app/routes/chat.py", "package.json"],
     lambda p: {"path": p}),
    ("read_file_metadata", ["how big is {p} and when was it last modified?", "check the metadata on {p}",
                             "what's the file size and mtime of {p}?", "get stat info for {p}, not the contents"],
     ["logs/benchmarks/server/chat-default.jsonl", "app/main.py", "models/gguf/qwen3-8b.gguf",
      "docs/phase0-measurements.md", "scripts/bench_server.py"],
     lambda p: {"path": p}),
    ("file_grep", ["search for '{pat}' in {d}", "grep '{pat}' inside {d}", "find every occurrence of {pat} under {d}"],
     [("TODO", "app/"), ("FIXME", "src/"), ("api_key", "."), ("class Router", "app/routes/"), ("import os", "scripts/")],
     None),  # handled specially, two-slot
    ("run_code", ["run this {lang} code: {code}", "execute: {code}", "test this snippet in {lang}: {code}"],
     [("python", "print(sum(range(50)))"), ("javascript", "console.log([1,2,3].reduce((a,b)=>a+b))"),
      ("python", "print(2**16)"), ("python", "print(sorted(['b','a','c']))"),
      ("javascript", "console.log(new Date().getFullYear())")],
     None),  # handled specially, two-slot
    ("memory_save", ["remember that {f}", "save this: {f}", "note for later: {f}"],
     ["I use zsh not bash", "my default editor is VS Code", "I want responses under 3 paragraphs",
      "remember I'm on Ubuntu 24.04", "my team's standup is at 9:30am"],
     lambda f: {"content": f}),
    ("memory_search", ["what did I tell you about my {q}?", "do you remember my {q}?",
                        "search memory for anything about my {q}", "what do you have saved about my {q}?"],
     ["editor preference", "shell preference", "OS", "standup time"],
     lambda f: {"query": f}),
    ("memory_delete", ["forget that I said {f}", "delete the memory about {f}", "remove the saved note that {f}"],
     ["I use zsh not bash", "my default editor is VS Code", "I want responses under 3 paragraphs",
      "I'm on Ubuntu 24.04"],
     lambda f: {"content": f}),
]


def build_examples(n, seed):
    rng = random.Random(seed)
    examples = []
    simple_buckets = [b for b in BUCKETS if b[3] is not None]
    per_bucket = max(1, n // len(BUCKETS))

    for name, templates, fills, argfn in simple_buckets:
        for _ in range(per_bucket):
            tmpl = rng.choice(templates)
            fill = rng.choice(fills)
            # find the single {X} placeholder name in the template
            m = re.search(r"\{(\w+)\}", tmpl)
            query = tmpl.format(**{m.group(1): fill})
            answer = [{"name": name, "arguments": argfn(fill)}]
            examples.append({"query": query, "tools": TOOLS_JSON,
                              "answers": json.dumps(answer, separators=(",", ":"))})

    # file_grep: two-slot (pattern, dir)
    grep_templates, grep_fills = BUCKETS[7][1], BUCKETS[7][2]
    for _ in range(per_bucket):
        tmpl = rng.choice(grep_templates)
        pat, d = rng.choice(grep_fills)
        query = tmpl.format(pat=pat, d=d)
        answer = [{"name": "file_grep", "arguments": {"pattern": pat, "path": d}}]
        examples.append({"query": query, "tools": TOOLS_JSON,
                          "answers": json.dumps(answer, separators=(",", ":"))})

    # run_code: two-slot (lang, code)
    code_templates, code_fills = BUCKETS[8][1], BUCKETS[8][2]
    while len(examples) < n:
        tmpl = rng.choice(code_templates)
        lang, code = rng.choice(code_fills)
        query = tmpl.format(lang=lang, code=code)
        answer = [{"name": "run_code", "arguments": {"language": lang, "code": code}}]
        examples.append({"query": query, "tools": TOOLS_JSON,
                          "answers": json.dumps(answer, separators=(",", ":"))})

    rng.shuffle(examples)
    return examples[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data_stress.jsonl")
    ap.add_argument("--n", type=int, default=90)
    args = ap.parse_args()

    examples = build_examples(args.n, seed=20260723)
    seen = set()
    deduped = []
    for ex in examples:
        if ex["query"] in seen:
            continue
        seen.add(ex["query"])
        deduped.append(ex)

    with open(args.out, "w") as f:
        for ex in deduped:
            f.write(json.dumps(ex) + "\n")

    from collections import Counter
    counts = Counter(json.loads(ex["answers"])[0]["name"] for ex in deduped)
    print(f"Wrote {len(deduped)} examples to {args.out} across {len(TOOLS)} tools (with overlapping names)")
    print("Per-tool counts:", dict(counts))


if __name__ == "__main__":
    main()
