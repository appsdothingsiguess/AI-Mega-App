#!/usr/bin/env python3
"""
Full finetune of FunctionGemma-270M on this app's real 6-tool registry,
using the same scripts/needle_training/data.jsonl the Qwen/Hammer dispatcher
eval and the needle rehearsal-finetune both used.

Why: FunctionGemma scored 0% zero-shot in the generic-prompt harness
(docs/phase0-measurements.md) because it expects tools declared via its own
<start_function_declaration> chat-template tokens and calls emitted as
<start_function_call>call:name{args}<end_function_call>, not a JSON-array
completion. Google/Distil Labs' published recipe reports 10-39% base ->
90-97% after finetuning, so the zero-shot FAIL doesn't rule it out -- this
script runs that finetune step using the tool-call data we already have.

Train/test split matches eval_llm_tool_calling.py: last N_TEST examples
held out, everything before that is train.

Usage:
  finetune_functiongemma.py [--epochs 6] [--lr 1e-4] [--out-dir <dir>]
"""
import argparse
import json
import random
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, Trainer
from torch.utils.data import Dataset

REPO = Path("/home/john/AI-Mega-App")
DATA = REPO / "scripts" / "needle_training" / "data.jsonl"
BASE_MODEL = "unsloth/functiongemma-270m-it"
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


class ToolCallDataset(Dataset):
    def __init__(self, examples, tokenizer, max_len=1024):
        self.tokenizer = tokenizer
        self.max_len = max_len
        self.examples = examples

    def __len__(self):
        return len(self.examples)

    def __getitem__(self, idx):
        ex = self.examples[idx]
        tools = openai_tools(ex["tools"])
        answers = json.loads(ex["answers"])
        tool_calls = [{"function": {"name": a["name"], "arguments": a["arguments"]}} for a in answers]

        prompt_messages = [{"role": "user", "content": ex["query"]}]
        full_messages = prompt_messages + [{"role": "assistant", "tool_calls": tool_calls}]

        prompt_text = self.tokenizer.apply_chat_template(
            prompt_messages, tools=tools, tokenize=False, add_generation_prompt=True)
        full_text = self.tokenizer.apply_chat_template(
            full_messages, tools=tools, tokenize=False, add_generation_prompt=False)

        prompt_ids = self.tokenizer(prompt_text, add_special_tokens=False)["input_ids"]
        full_ids = self.tokenizer(full_text, add_special_tokens=False)["input_ids"]

        full_ids = full_ids[: self.max_len]
        labels = list(full_ids)
        mask_len = min(len(prompt_ids), len(labels))
        for i in range(mask_len):
            labels[i] = -100

        return {
            "input_ids": full_ids,
            "attention_mask": [1] * len(full_ids),
            "labels": labels,
        }


def collate(batch, pad_id):
    max_len = max(len(b["input_ids"]) for b in batch)
    input_ids, attn, labels = [], [], []
    for b in batch:
        pad = max_len - len(b["input_ids"])
        input_ids.append(b["input_ids"] + [pad_id] * pad)
        attn.append(b["attention_mask"] + [0] * pad)
        labels.append(b["labels"] + [-100] * pad)
    return {
        "input_ids": torch.tensor(input_ids),
        "attention_mask": torch.tensor(attn),
        "labels": torch.tensor(labels),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--out-dir", default=str(REPO / "logs" / "benchmarks" / "functiongemma-finetuned"))
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--full-data", action="store_true",
                     help="train on all examples in data.jsonl with no internal holdout carve-out "
                          "(use when evaluating against an external freshly-generated holdout set instead)")
    args = ap.parse_args()

    random.seed(args.seed)
    torch.manual_seed(args.seed)

    lines = DATA.read_text().splitlines()
    all_examples = [json.loads(l) for l in lines]
    if args.full_data:
        train_examples = all_examples
        print(f"Loaded {len(all_examples)} examples: training on all {len(train_examples)} (full-data mode, no internal holdout)")
    else:
        train_examples = all_examples[:-N_TEST]
        print(f"Loaded {len(all_examples)} examples: {len(train_examples)} train, {N_TEST} held-out (untouched)")

    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, dtype=torch.bfloat16).to("cuda:0")

    train_ds = ToolCallDataset(train_examples, tokenizer)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    targs = TrainingArguments(
        output_dir=str(out_dir / "checkpoints"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        learning_rate=args.lr,
        warmup_ratio=0.1,
        lr_scheduler_type="cosine",
        logging_steps=10,
        save_strategy="no",
        bf16=True,
        report_to=[],
        remove_unused_columns=False,
    )

    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=train_ds,
        data_collator=lambda batch: collate(batch, tokenizer.pad_token_id or 0),
    )
    trainer.train()

    final_dir = out_dir / "final"
    model.save_pretrained(final_dir)
    tokenizer.save_pretrained(final_dir)
    print(f"DONE: saved finetuned model to {final_dir}")


if __name__ == "__main__":
    main()
