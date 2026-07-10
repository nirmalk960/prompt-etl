"""Detect available compute resources from the OS / container.

Used by `EngineRouter` to size decisions to the host. Works on:
  - cgroups v2 (most modern Linux containers)
  - cgroups v1
  - bare metal (psutil-style /proc/meminfo)
"""

from __future__ import annotations

import os
from functools import lru_cache

_CGROUP_V2_LIMIT = "/sys/fs/cgroup/memory.max"
_CGROUP_V1_LIMIT = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
_PROC_MEMINFO = "/proc/meminfo"

# Sentinel for "unlimited" in cgroups v1: a very large number close to 2^63-1.
_CGROUP_V1_UNLIMITED_THRESHOLD = 1 << 62


def _read_int(path: str) -> int | None:
    try:
        with open(path) as fh:
            raw = fh.read().strip()
    except OSError:
        return None
    if raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _meminfo_total() -> int | None:
    try:
        with open(_PROC_MEMINFO) as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    return int(parts[1]) * 1024
    except OSError:
        return None
    return None


def _psutil_total() -> int | None:
    """Best-effort host detection. Falls back to a sane default on macOS dev."""
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        # macOS dev: try sysctl
        try:
            import subprocess

            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            return int(out)
        except Exception:
            return None
    return int(psutil.virtual_memory().total)


@lru_cache(maxsize=1)
def detect_available_memory_bytes(override: int | None = None) -> int:
    """Return the memory budget this process should plan against.

    Resolution order:
      1. explicit override (settings.engine_ram_override_bytes)
      2. cgroups v2 memory.max
      3. cgroups v1 memory.limit_in_bytes (if not the unlimited sentinel)
      4. /proc/meminfo MemTotal
      5. host detection (psutil / sysctl)
      6. final fallback: 4 GiB (so the router can still decide)
    """
    if override is not None and override > 0:
        return override

    v2 = _read_int(_CGROUP_V2_LIMIT)
    if v2 is not None and v2 > 0:
        return v2

    v1 = _read_int(_CGROUP_V1_LIMIT)
    if v1 is not None and 0 < v1 < _CGROUP_V1_UNLIMITED_THRESHOLD:
        return v1

    mi = _meminfo_total()
    if mi is not None and mi > 0:
        return mi

    host = _psutil_total()
    if host is not None and host > 0:
        return host

    return 4 * 1024 * 1024 * 1024  # 4 GiB final fallback


def detect_cpu_count() -> int:
    return os.cpu_count() or 1
