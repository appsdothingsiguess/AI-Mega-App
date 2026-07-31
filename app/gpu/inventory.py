"""GPU inventory via nvidia-smi (PLAN.md §4.1, docs/FEATURES.md F14).

Graceful degradation: if nvidia-smi is absent or non-zero exit (dev
machines, CI) returns []. The pure `parse_nvidia_smi_csv` is split out so
tests can exercise the parser without subprocesses.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class GPUInfo:
    index: int
    name: str
    mem_total_mb: int
    mem_free_mb: int


def parse_nvidia_smi_csv(text: str) -> list[GPUInfo]:
    """Parse the output of nvidia-smi --format=csv,noheader,nounits.

    Each line: index, name, memory.total [MiB], memory.free [MiB]
    Units are stripped by nounits; values are integers.
    """
    results: list[GPUInfo] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        try:
            results.append(
                GPUInfo(
                    index=int(parts[0]),
                    name=parts[1],
                    mem_total_mb=int(parts[2]),
                    mem_free_mb=int(parts[3]),
                )
            )
        except ValueError:
            continue
    return results


async def fetch_inventory() -> list[GPUInfo]:
    """Run nvidia-smi and return GPU info. Returns [] on any failure."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=index,name,memory.total,memory.free",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return []
        return parse_nvidia_smi_csv(stdout.decode())
    except (FileNotFoundError, OSError):
        return []
