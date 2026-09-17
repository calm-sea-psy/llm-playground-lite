"""항목 시작 직전의 메모리 여유를 읽는다.

OOM으로 실패했다고 **판정**하려면 오류 문구만으로는 부족하고, 메모리가 실제로
모자랐다는 수치가 함께 있어야 한다. 그런데 OOM이 나면 Ollama가 메모리를
풀어버려서 실패 **직후**에 재면 최고치가 남아 있지 않다 — 그래서 요청 **전에**
읽어 기록해둔다.

Ollama API는 모델 크기만 주고 남은 메모리는 주지 않으므로 따로 읽는다:
VRAM은 `nvidia-smi`, 시스템 메모리는 Windows `GlobalMemoryStatusEx`(ctypes —
새 의존성 없이). 어느 쪽이든 읽지 못하면 `None`이고, 그러면 메모리 근거가
성립하지 않아 OOM 판정도 성립하지 않는다 — 모델에게 불리하지 않은 쪽으로
실패하게 둔다(분류 불가의 기본값은 `infra`).
"""

import ctypes
import subprocess
import sys
from typing import Any

import bench_config as cfg
import ollama_client

# KV 캐시 추정이 모델 정보에 없을 때 쓰는 보수적 하한 — 토큰당 바이트.
# 추정이 과소하면 "모자랐다"가 덜 성립할 뿐이라(= OOM 판정이 덜 붙음) fail-safe 방향이다.
_FALLBACK_KV_BYTES_PER_TOKEN = 0


def free_vram_bytes() -> int | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    values = [line.strip() for line in out.splitlines() if line.strip()]
    try:
        return sum(int(v) for v in values) * 1024 * 1024  # MiB
    except ValueError:
        return None


def free_ram_bytes() -> int | None:
    if sys.platform != "win32":
        return None

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    try:
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except (AttributeError, OSError):
        return None
    return int(status.ullAvailPhys)


def model_size_bytes(model: str) -> int | None:
    try:
        for m in ollama_client.list_models():
            if m.get("name") == model or m.get("model") == model:
                return int(m.get("size") or 0) or None
    except Exception:  # noqa: BLE001 — 근거 수집 실패는 판정을 막을 뿐 실행을 막지 않는다
        return None
    return None


def _loaded_size_bytes(model: str) -> int:
    try:
        for m in ollama_client.list_loaded():
            if m.get("name") == model or m.get("model") == model:
                return int(m.get("size") or 0)
    except Exception:  # noqa: BLE001
        return 0
    return 0


def _kv_bytes_per_token(model: str) -> int:
    """`/api/show`의 `model_info`로 KV 캐시 크기를 추정한다(레이어 × KV 헤드 ×
    헤드 차원 × 2(K·V) × 2바이트(f16)). 필드가 없으면 0 — 과소 추정 쪽이 안전하다."""
    try:
        info = ollama_client.show_model(model).get("model_info") or {}
    except Exception:  # noqa: BLE001
        return _FALLBACK_KV_BYTES_PER_TOKEN

    def find(suffix: str) -> int | None:
        for k, v in info.items():
            if k.endswith(suffix) and isinstance(v, int):
                return v
        return None

    layers = find(".block_count")
    kv_heads = find(".attention.head_count_kv")
    key_len = find(".attention.key_length")
    if not (layers and kv_heads and key_len):
        return _FALLBACK_KV_BYTES_PER_TOKEN
    return layers * kv_heads * key_len * 2 * 2


def snapshot(model: str) -> dict[str, Any]:
    """항목 시작 직전의 기록. `shortfall`은 필요 추정치가 가용 합계보다 클 때만 True —
    어느 값이든 없으면 None(근거 없음)이다."""
    size = model_size_bytes(model)
    vram, ram = free_vram_bytes(), free_ram_bytes()
    required = None
    if size is not None:
        required = size + _kv_bytes_per_token(model) * cfg.NUM_CTX
    available = (vram or 0) + (ram or 0) if (vram is not None or ram is not None) else None
    # 이미 올라가 있는 모델은 자기 몫의 메모리를 쥐고 있다 — 그만큼을 "가용"에
    # 되돌려놓지 않으면 품질 항목(모델이 이미 로드된 상태)마다 가짜 부족이 뜬다.
    loaded = _loaded_size_bytes(model)
    if available is not None and loaded:
        available += loaded
    shortfall = None if required is None or available is None else required > available
    return {
        "model_size_bytes": size,
        "num_ctx": cfg.NUM_CTX,
        "required_bytes_estimate": required,
        "free_vram_bytes": vram,
        "free_ram_bytes": ram,
        "already_loaded_bytes": loaded,
        "shortfall": shortfall,
    }
