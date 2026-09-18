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
    # OS와 GPU 백엔드(CUDA)도 값이다 — 같은 기계라도 드라이버·런타임이 바뀌면 같은 입력에 다른 답이 나올 수 있다
    return {"cpu": _cpu_name(), "logical_cpus": os.cpu_count(), "ram_bytes": _ram_bytes(), "gpus": _gpus(),
            "os": os_name(), "cuda": cuda_version()}


def os_name() -> str:
    """운영체제 이름과 판 — `Windows 11 (10.0.26200)`처럼 적는다(판이 이름에 이미 들어 있으면 이름만)."""
    release, version = platform.release(), platform.version()
    return f"{platform.system()} {release}".strip() + (f" ({version})" if version and version != release else "")


def cuda_version() -> str | None:
    """GPU 백엔드 — `nvidia-smi`가 적는 CUDA 판. 도구가 없거나 그 줄이 없으면 None(못 읽음)."""
    try:
        smi = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"CUDA Version:\s*([0-9.]+)", smi)
    return match.group(1) if match else None


def git_commit() -> dict[str, Any] | None:
    """이 도구(저장소)의 지금 커밋 — 리포트와 측정이 어느 코드로 나온 것인지 되짚는 값. 작업 트리에 손댄 것이 있으면 `dirty`.
    git이 없거나 저장소가 아니면 None(기록 없음)."""
    root = Path(__file__).resolve().parent.parent
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                                capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return {"sha": sha, "dirty": bool(status)} if sha else None


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


def power_snapshot() -> dict[str, Any]:
    """실행을 시작할 때의 전원 상태 — AC/배터리와 GPU 전력 한계. 같은 기계·같은 모델에서도 tok/s와 GPU 평균 전력은 전원에 따라
    달라진다(노트북 GPU는 배터리·전원 모드에 따라 한계가 바뀐다). **시작 시점 한 번**이라 실행 중에 전원이 바뀐 것은 남지 않는다."""
    return {**_power_source(), "gpus": _gpu_power_limits()}


class _PowerStatus(ctypes.Structure):
    _fields_ = [
        ("ac_line_status", ctypes.c_ubyte),
        ("battery_flag", ctypes.c_ubyte),
        ("battery_life_percent", ctypes.c_ubyte),
        ("system_status_flag", ctypes.c_ubyte),
        ("battery_life_time", ctypes.c_ulong),
        ("battery_full_life_time", ctypes.c_ulong),
    ]


def _power_source() -> dict[str, Any]:
    """`{ac_power, battery_percent}` — 못 읽으면 None. Windows는 `GetSystemPowerStatus`, 그 밖은 읽지 않는다."""
    if platform.system() != "Windows":
        return {"ac_power": None, "battery_percent": None}
    status = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
        return {"ac_power": None, "battery_percent": None}
    return power_source_from_status(status.ac_line_status, status.battery_flag, status.battery_life_percent)


def power_source_from_status(ac_line: int, battery_flag: int, percent: int) -> dict[str, Any]:
    """`GetSystemPowerStatus` 값의 뜻 — AC 0 끊김 · 1 연결 · 그 밖 모름. 배터리 플래그 128은 배터리 없음, 255와 잔량 255는 모름."""
    ac_power = {0: False, 1: True}.get(ac_line)
    battery_percent = None if battery_flag in (128, 255) or percent == 255 else int(percent)
    return {"ac_power": ac_power, "battery_percent": battery_percent}


def _gpu_power_limits() -> list[dict[str, Any]] | None:
    """NVIDIA GPU마다 전력 한계(W) — `power.limit`(설정한 한계) · `enforced.power.limit`(실제로 걸린 한계) · `power.default_limit`.
    노트북 GPU는 `power.limit`이 `[N/A]`로 나오고 걸린 한계만 읽히는 일이 있어 셋을 다 남긴다. 칸마다 못 읽으면 None, 도구가 없거나
    실패하면 None(못 읽음), GPU가 없으면 빈 목록이다(`_gpus`와 같은 가름)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,power.limit,enforced.power.limit,power.default_limit", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    gpus = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            return None  # 모양이 다르면 읽지 않는다
        name, limit, enforced, default = parts
        gpus.append({"name": name, "power_limit_watts": _watts(limit), "enforced_power_limit_watts": _watts(enforced),
                     "default_power_limit_watts": _watts(default)})
    return gpus


def _watts(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None  # `[N/A]`·`[Not Supported]`


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
