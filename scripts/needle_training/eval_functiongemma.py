#!/usr/bin/env python3
"""
Eval for FunctionGemma-270M using its NATIVE chat-template tool-call format
(<start_function_call>call:name{args}<end_function_call>), not the generic
JSON-array prompt eval_llm_tool_calling.py uses for the Qwen/Hammer
candidates (that harness scores FunctionGemma at 0% because the format
doesn't match -- see docs/phase0-measurements.md).

Same held-out 60 examples, same call_f1/exact_match metric as
eval_llm_tool_calling.py, for direct comparability.

Usage:
  eval_functiongemma.py --model unsloth/functiongemma-270m-it --label base
  eval_functiongemma.py --model logs/benchmarks/functiongemma-finetuned/final --label finetuned
"""
import argparse
import json
import re
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

REPO = Path("/home/john/AI-Mega-App")
DATA = REPO / "scripts" / "needle_training" / "data.jsonl"
N_TEST = 60


def openai_tools(tools_json):
    tools = json.loads(tools_json)
    out = []
    for t in tools:
        props = {}
        required = []
        for pname, pinfo in t["parameters"].items():
            props[pname] = {"type": pinfo["type"], "description": pinfo["description"]}
            if pinfo.get("required"):
                required.append(pname)
        out.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": {"type": "object", "properties": props, "required": required},
            },
        })
    return out


def parse_function_calls(text):
    # <start_function_call>call:name{k:v,...}<end_function_call>
    calls = []
    for m in re.finditer(r"call:([a-zA-Z_][a-zA-Z0-9_]*)\{(.*?)\}<end_function_call>", text, re.S):
        name, argstr = m.group(1), m.group(2)
        args = {}
        # args are k:v comma-separated; v may be <escape>...<escape>-quoted string
        for am in re.finditer(r"([a-zA-Z_][a-zA-Z0-9_]*):(?:<escape>(.*?)<escape>|([^,}]+))", argstr):
            k, sv, nv = am.group(1), am.group(2), am.group(3)
            args[k] = sv if sv is not None else nv
        calls.append({"name": name, "arguments": args})
    return calls


def _normalize_value(v):
    if isinstance(v, str):
        try:
            return str(float(v))
        except ValueError:
            return v.strip().lower()
    if isinstance(v, float):
        return str(v)
    return v


def call_key(c):
    args = {k: _normalize_value(v) for k, v in c.get("arguments", {}).items()}
    return json.dumps({"name": c.get("name"), "arguments": args}, sort_keys=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--n-test", type=int, default=N_TEST)
    ap.add_argument("--max-new-tokens", type=int, default=150)
    args = ap.parse_args()

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForCausalLM.from_pretrained(args.model, dtype=torch.bfloat16).to("cuda:0")
    model.eval()

    lines = DATA.read_text().splitlines()
    test = [json.loads(l) for l in lines[-args.n_test:]]

    out_path = REPO / "logs" / "benchmarks" / "server" / f"tool-eval-functiongemma-{args.label}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    tp = fp = fn = exact = parse_ok = 0
    latencies = []

    for i, ex in enumerate(test):
        tools = openai_tools(ex["tools"])
        ref_calls = json.loads(ex["answers"])
        messages = [{"role": "user", "content": ex["query"]}]
        prompt = tokenizer.apply_chat_template(messages, tools=tools, tokenize=False, add_generation_prompt=True)
        inputs = tokenizer(prompt, return_tensors="pt", add_special_tokens=False).to("cuda:0")

        t0 = time.perf_counter()
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=args.max_new_tokens, do_sample=False,
                                  pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id)
        wall = time.perf_counter() - t0
        latencies.append(wall)

        gen_text = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=False)
        pred_calls = parse_function_calls(gen_text)
        if pred_calls:
            parse_ok += 1

        ref_keys = {call_key(c) for c in ref_calls}
        pred_keys = {call_key(c) for c in pred_calls}
        tp += len(pred_keys & ref_keys)
        fp += len(pred_keys - ref_keys)
        fn += len(ref_keys - pred_keys)
        is_exact = ref_keys == pred_keys and len(ref_keys) > 0
        if is_exact:
            exact += 1

        rec = {"i": i, "query": ex["query"], "ref": ref_calls, "pred": pred_calls,
               "raw_output": gen_text, "latency_s": wall, "exact": is_exact}
        with open(out_path, "a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"[{i}] {'OK' if is_exact else 'WRONG'} query={ex['query'][:50]!r} ref={ref_calls} pred={pred_calls}")

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    n = len(test)
    summary = {
        "label": args.label, "model": args.model, "n_test": n,
        "call_f1": round(f1 * 100, 1), "exact_match": round(100 * exact / n, 1),
        "json_parse_rate": round(100 * parse_ok / n, 1),
        "avg_latency_s": round(sum(latencies) / len(latencies), 3),
    }
    print("SUMMARY", json.dumps(summary))


if __name__ == "__main__":
    main()
