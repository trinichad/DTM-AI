"""Host resource stats for the dashboard — CPU / memory / disk / GPU of the box DTM AI runs on.

Read-only platform telemetry about OUR OWN host (not client systems). Stdlib-only, with a single
FIXED `nvidia-smi` invocation for GPU (hardcoded args, never LLM-controlled — Rule #6 is about the
agent, this is platform code). Every probe fails closed to None/[] so a missing source never 500s.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from typing import Any, Optional


def _num(s: str) -> Optional[float]:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _cpu_percent(sample: float = 0.12) -> Optional[float]:
    """Busy % over a short sample via /proc/stat (Linux); else derive from load average."""
    def _read():
        with open("/proc/stat", encoding="utf-8") as f:
            vals = [int(x) for x in f.readline().split()[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        return idle, sum(vals)
    try:
        i1, t1 = _read()
        time.sleep(sample)
        i2, t2 = _read()
        dt = t2 - t1
        if dt > 0:
            return round(100.0 * (1 - (i2 - i1) / dt), 1)
    except (OSError, IndexError, ValueError):
        pass
    try:
        cores = os.cpu_count() or 1
        return round(min(100.0, 100.0 * os.getloadavg()[0] / cores), 1)
    except (OSError, AttributeError):
        return None


def _memory() -> Optional[dict[str, Any]]:
    try:
        info: dict[str, int] = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                k, _, v = line.partition(":")
                info[k] = int(v.strip().split()[0])  # kB
        total = info.get("MemTotal", 0) / 1024 / 1024
        avail = info.get("MemAvailable", info.get("MemFree", 0)) / 1024 / 1024
        used = total - avail
        return {"used_gb": round(used, 1), "total_gb": round(total, 1),
                "percent": round(100.0 * used / total, 1) if total else None}
    except (OSError, ValueError):
        return None


def _disk(path: str = "/") -> Optional[dict[str, Any]]:
    try:
        u = shutil.disk_usage(path)
        return {"used_gb": round(u.used / 1e9, 1), "total_gb": round(u.total / 1e9, 1),
                "percent": round(100.0 * u.used / u.total, 1) if u.total else None}
    except OSError:
        return None


def _gpu() -> list[dict[str, Any]]:
    """NVIDIA GPUs via a fixed nvidia-smi query. [] if no GPU / tool absent."""
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode != 0:
            return []
        gpus = []
        for line in out.stdout.strip().splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) >= 5:
                gpus.append({"name": p[0], "util_percent": _num(p[1]),
                             "mem_used_mb": _num(p[2]), "mem_total_mb": _num(p[3]),
                             "temp_c": _num(p[4])})
        return gpus
    except (OSError, subprocess.SubprocessError):
        return []


def collect() -> dict[str, Any]:
    """One snapshot of host resources. Always returns a dict; absent sources are None/[]."""
    try:
        load = list(os.getloadavg())
    except (OSError, AttributeError):
        load = None
    return {
        "host": os.uname().nodename if hasattr(os, "uname") else None,
        "cpu": {"percent": _cpu_percent(), "cores": os.cpu_count(), "load": load},
        "memory": _memory(),
        "disk": _disk(),
        "gpu": _gpu(),
    }
