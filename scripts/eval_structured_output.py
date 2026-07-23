#!/usr/bin/env python3
"""
Phase-0.5 Test 7 item 6: JSON/structured-output reliability beyond tool-
calling. Tool-call JSON reliability is already measured (call_f1/exact_match/
json_parse_rate on the 6-tool set, scripts/needle_training/). This checks
plain structured-JSON output against an arbitrary schema -- flat objects,
nested objects, arrays, enums, optional fields -- since the app may need
structured JSON outside the tool-calling path (e.g. classifier/utility
outputs).

Uses a minimal hand-rolled schema validator (type + required-keys + enum
checks) to avoid adding the `jsonschema` package as a new dependency; if that
package is later approved, swap validate() for jsonschema.validate().

Usage:
  eval_structured_output.py --label chat-default --port 8899 [--n-predict 300]

Writes per-item + summary to logs/benchmarks/quality/structured_output.jsonl
"""
import argparse, json, re, sys, time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bench_server import http_json, log  # noqa: E402

REPO = Path("/home/john/AI-Mega-App")
OUTDIR = REPO / "logs" / "benchmarks" / "quality"
PROMPTS_PATH = REPO / "scripts" / "eval_data" / "structured_output_prompts.json"

_TYPE_MAP = {
    "string": str, "integer": int, "number": (int, float), "boolean": bool,
    "array": list, "object": dict,
}


def validate(value, schema, path="$"):
    """Minimal recursive validator: type + required + enum. Returns list of
    error strings, empty if valid."""
    errors = []
    expected_type = schema.get("type")
    py_type = _TYPE_MAP.get(expected_type)
    if py_type is not None and not isinstance(value, py_type):
        # bool is a subclass of int in python -- don't let a bool satisfy "integer"/"number"
        if not (expected_type in ("integer", "number") and isinstance(value, bool)):
            return [f"{path}: expected {expected_type}, got {type(value).__name__}"]
        errors.append(f"{path}: expected {expected_type}, got bool")

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum {schema['enum']}")

    if expected_type == "object" and isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}.{req}: missing required field")
        for key, subschema in schema.get("properties", {}).items():
            if key in value:
                errors.extend(validate(value[key], subschema, f"{path}.{key}"))

    if expected_type == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(value):
                errors.extend(validate(item, item_schema, f"{path}[{i}]"))

    return errors


def extract_json_object(text):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--model-path", default=None)
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--n-predict", type=int, default=300)
    ap.add_argument("--request-timeout", type=int, default=120)
    args = ap.parse_args()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = OUTDIR / "structured_output.jsonl"
    items = json.loads(PROMPTS_PATH.read_text())

    n_parse_ok = 0
    n_schema_ok = 0

    for item in items:
        t0 = time.perf_counter()
        try:
            body, wall = http_json(
                f"http://127.0.0.1:{args.port}/v1/chat/completions",
                {"messages": [{"role": "user", "content": item["prompt"]}],
                 "max_tokens": args.n_predict, "temperature": 0, "stream": False},
                args.request_timeout,
            )
            raw = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = extract_json_object(raw)
        except Exception as e:
            raw, parsed, wall = "", None, time.perf_counter() - t0
            log(f"  error on {item['id']}: {e}")

        parse_ok = parsed is not None
        errors = validate(parsed, item["json_schema"]) if parse_ok else ["not parseable as JSON"]
        schema_ok = parse_ok and not errors
        n_parse_ok += parse_ok
        n_schema_ok += schema_ok

        rec = {
            "ts": datetime.now(timezone.utc).isoformat(), "label": args.label,
            "item_id": item["id"], "raw_response": raw, "parsed": parsed,
            "parse_ok": parse_ok, "schema_valid": schema_ok, "errors": errors,
            "latency_s": wall,
        }
        with open(jsonl_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        log(f"  {'OK' if schema_ok else 'FAIL'} {item['id']}: parse_ok={parse_ok} schema_ok={schema_ok} errors={errors}")

    n = len(items)
    summary = {
        "event": "summary", "label": args.label, "n": n,
        "json_parse_rate": round(n_parse_ok / max(n, 1), 4),
        "schema_valid_rate": round(n_schema_ok / max(n, 1), 4),
    }
    log(f"SUMMARY {json.dumps(summary)}")
    with open(jsonl_path, "a") as f:
        f.write(json.dumps(summary) + "\n")


if __name__ == "__main__":
    main()
