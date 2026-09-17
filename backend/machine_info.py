# -*- coding: utf-8 -*-
"""측정한 기계의 사양과 소프트웨어 — tok/s·모델 로드 시간·메모리는 기계에 딸린 값이라, 결과에 **어떤 사양에서 쟀는지**를 남긴다.

실행을 시작할 때만 적을 수 있고 나중에 붙일 수 없다(`bench_config.measurement_machine`과 같은 까닭). 못 읽은 칸은
None으로 남긴다 — 지어내지 않고, 사양을 못 읽었다고 측정을 막지도 않는다.
"""

from __future__ import annotations

import ctypes
import importlib.metadata
import os
import platform
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

PYPROJECT = Path(__file__).parent / "pyproject.toml"


def hardware_snapshot() -> dict[str, Any]:
    return {"cpu": _cpu_name(), "logical_cpus": os.cpu_count(), "ram_bytes": _ram_bytes(), "gpus": _gpus()}


def _cpu_name() -> str | None:
    """`platform.processor()`는 Windows에서 제품명이 아니라 `Intel64 Family 6 Model …`을 준다 — 레지스트리의 이름을 먼저 본다."""
    if platform.system() == "Windows":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip() or None
        except OSError:
            pass
    return platform.processor() or None


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("memory_load", ctypes.c_ulong),
        ("total_phys", ctypes.c_ulonglong),
        ("avail_phys", ctypes.c_ulonglong),
        ("total_page_file", ctypes.c_ulonglong),
        ("avail_page_file", ctypes.c_ulonglong),
        ("total_virtual", ctypes.c_ulonglong),
        ("avail_virtual", ctypes.c_ulonglong),
        ("avail_extended_virtual", ctypes.c_ulonglong),
    ]


def _ram_bytes() -> int | None:
    if platform.system() == "Windows":
        status = _MemoryStatus(length=ctypes.sizeof(_MemoryStatus))
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.total_phys)
        return None
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None


def _gpus() -> list[dict[str, Any]] | None:
    """NVIDIA GPU — 이름·VRAM·드라이버. 도구가 없거나 실패하면 None(못 읽음), 돌았는데 GPU가 없으면 빈 목록이다 —
    둘을 같은 값으로 두면 `GPU가 없다`와 `GPU를 못 봤다`가 한 말이 된다."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            return None  # 모양이 다르면 읽지 않는다 — 잘못 읽은 사양을 남기느니 못 읽었다고 남긴다
        name, mib, driver = parts
        try:
            vram = int(float(mib)) * 1024 * 1024
        except ValueError:
            vram = None
        gpus.append({"name": name, "vram_bytes": vram, "driver": driver or None})
    return gpus


def software_snapshot() -> dict[str, Any]:
    """Python과 이 앱이 선언한 패키지의 **설치된** 버전 — 채점이 기대는 라이브러리(`jsonschema` 등)가 바뀌면 같은 답에도
    값이 달라질 수 있다. 목록은 `pyproject.toml`의 의존성에서 읽는다(여기 따로 적으면 둘이 어긋난다). 설치되지 않은 패키지는
    None이다 — 선언과 설치가 다르다는 것도 기록이다."""
    return {"python": platform.python_version(), "packages": {name: _installed(name) for name in _declared_packages()}}


def _declared_packages() -> list[str]:
    try:
        deps = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["dependencies"]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return []
    return [m.group(0) for spec in deps if (m := re.match(r"[A-Za-z0-9._-]+", spec))]


def _installed(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
