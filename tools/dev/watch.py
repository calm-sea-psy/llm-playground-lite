# -*- coding: utf-8 -*-
"""개발용 감시 서버 — 백엔드와 프런트를 지켜보다 꺼진 쪽을 다시 띄운다.

측정과는 아무 상관이 없다. 재는 동안 사람이 화면을 지켜볼 수 없는데, 그 사이 한쪽이 조용히 사라지는
일이 실제로 있었다(실행은 끝났는데 딤드 창이 안 걷혔고, 그때 프런트 개발 서버가 내려가 있었다).
감시를 한쪽 서버 안에 넣지 않고 따로 두는 까닭은 하나다 — **감시하는 쪽이 감시받는 쪽과 함께 죽으면
안 된다.** 이 프로세스는 포트를 들여다보고 자식을 띄우는 일만 해서, 무너질 구석이 가장 적다.

규칙 넷.

- **남의 서버는 건드리지 않는다.** 포트가 이미 열려 있으면 아무것도 하지 않는다 — 사람이 띄운 것이다.
  우리가 띄운 자식만 지켜보고, 그 자식이 꺼졌을 때만 다시 띄운다.
- **연달아 실패하면 물러선다.** `node_modules`가 없는 것처럼 다시 띄운다고 풀리지 않는 까닭이 있으면
  무한히 되살리기는 로그만 채운다. 물러설 때 까닭을 한 줄 남긴다.
- **상태를 내보인다.** `http://127.0.0.1:8765/`에 지금 상태(떠 있나·몇 번 살렸나·마지막에 한 일)를
  JSON으로 준다. 감시가 무엇을 하고 있는지 사람이 볼 수 없으면 감시를 믿을 수 없다.
- **끝낼 때 자기 자식만 데려간다.** Ctrl+C로 멈추면 이 프로세스가 띄운 것만 끈다.

    python tools/dev/watch.py                 # 둘 다 감시
    python tools/dev/watch.py --only frontend # 한쪽만
    python tools/dev/watch.py --status-port 0 # 상태 페이지 없이
"""

from __future__ import annotations

import argparse
import json
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WATCH_SEC = 3.0
# 띄운 뒤 포트가 열릴 때까지 기다려 주는 시간 — Vite는 1초 안에 열리지만 첫 실행은 의존성을 훑는다
GRACE_SEC = 30.0
# 다시 띄우기 전에 쉬는 시간 — 같은 까닭으로 계속 실패할 때 간격을 벌린다
BACKOFF_SEC = (2, 5, 10, 30, 60)
GIVE_UP_AFTER = 5
STATUS_PORT = 8765
# 건강 확인 — 포트가 열려 있어도 답을 못 하면 떠 있는 것이 아니다(먹통). 한 번 늦었다고 끄지 않는다:
# 이어서 이만큼 실패해야 `꺼짐`으로 본다
HEALTH_TIMEOUT_SEC = 5.0
HEALTH_FAILS_BEFORE_DOWN = 3
# 측정이 도는 중이면 더 참는다 — 몇 초 늦는 것과 먹통을 가르지 못해 다시 띄우면 몇 십 분이 날아간다
HEALTH_FAILS_WHEN_BUSY = 10

OK, WAIT, RESTART, GIVE_UP = "ok", "wait", "restart", "give_up"


class AlreadyWatching(RuntimeError):
    """상태 포트를 누가 쥐고 있다 — 감시가 이미 돌고 있다는 뜻이다."""


def decide(*, is_open: bool, child_running: bool, since_start: float, failures: int) -> str:
    """지금 무엇을 할 것인가 — 프로세스도 소켓도 모르는 순수 판단이라 시험에서 그대로 돌린다.

    포트가 열려 있으면 할 일이 없다(누가 띄웠든). 우리 자식이 막 떠서 아직 포트를 안 열었으면 기다린다.
    그 밖에는 다시 띄우되, 연달아 실패한 횟수가 차면 물러선다."""
    if is_open:
        return OK
    if failures >= GIVE_UP_AFTER:
        return GIVE_UP
    if child_running and since_start < GRACE_SEC:
        return WAIT
    return RESTART


def backoff_sec(failures: int) -> float:
    """다시 띄우기 전에 쉬는 시간 — 실패가 쌓일수록 길어지고 마지막 값에서 멈춘다."""
    return float(BACKOFF_SEC[min(failures, len(BACKOFF_SEC) - 1)])


# 듣고 있는 자리를 볼 때 보는 주소들 — **IPv4와 IPv6를 모두** 본다. 이 기계의 Vite는 `[::1]:5173`에만
# 붙어서 IPv4만 보면 늘 `꺼짐`이고, 감시가 멀쩡한 서버를 끝없이 다시 띄운다(실제로 그렇게 돌았다).
# 이름(`localhost`)을 풀지 않고 숫자 주소를 쓰는 것은 그대로다 — 이름 풀이가 늦는 자리가 있다
PROBE_HOSTS = ("127.0.0.1", "::1")


def port_open(port: int, hosts: tuple[str, ...] = PROBE_HOSTS) -> bool:
    """그 포트에 무엇인가 듣고 있는가 — 한 주소라도 받으면 떠 있는 것이다."""
    for host in hosts:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.3)
                if probe.connect_ex((host, port)) == 0:
                    return True
        except OSError:  # 그 주소 계열이 없는 기계 — 다음 주소로 넘어간다
            continue
    return False


def use_utf8_console() -> None:
    """Windows 콘솔의 기본 인코딩(cp949)으로는 이 파일의 한글 로그를 못 찍는다 — 실제로 감시가 첫 줄에서
    죽었다. 감시가 로그 한 줄 때문에 넘어지면 감시가 아니다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # 파이프로 받는 자리 등 — 찍는 것이 목적이라 조용히 넘어간다
            pass


def log(name: str, message: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} [{name}] {message}", flush=True)


def health_ok(url: str, marker: str, timeout: float = HEALTH_TIMEOUT_SEC) -> tuple[bool, dict]:
    """그 자리가 답하는가 — `(답했나, 읽은 값)`. 200이고 `marker`가 본문에 있으면 답한 것이다.

    포트만 보면 떠 있는데 답만 안 하는 상태를 못 잡는다. 반대로 건강 확인만 믿으면 뜨는 중인 서버를
    먹통으로 읽으므로, 부르는 쪽이 포트와 함께 본다."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as reply:
            if reply.status != 200:
                return False, {}
            body = reply.read(4096).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError):
        return False, {}
    if marker not in body:
        return False, {}
    try:
        return True, json.loads(body)
    except json.JSONDecodeError:
        return True, {}  # JSON이 아니어도 답은 한 것이다


@dataclass
class Service:
    """감시하는 서버 하나. `cmd`는 우리가 띄울 때 쓰고, 포트와 건강 확인 자리로 살아 있는지 본다."""

    name: str
    port: int
    cmd: list[str]
    cwd: Path
    health_path: str = "/"
    health_marker: str = ""
    child: subprocess.Popen | None = None
    started_at: float = 0.0
    failures: int = 0
    restarts: int = 0
    last_action: str = "아직 안 봄"
    last_error: str | None = None
    adopted: bool = False  # 우리가 띄우지 않았는데 이미 떠 있던 것
    health_fails: int = 0
    busy: bool = False  # 마지막으로 답했을 때 측정이 돌고 있었나

    def child_running(self) -> bool:
        return self.child is not None and self.child.poll() is None

    def health_urls(self) -> list[str]:
        """두드릴 자리들 — 포트를 볼 때처럼 **두 주소 계열을 다 본다**. 이 기계의 Vite는 `[::1]`에만 붙어서
        IPv4로만 물으면 답이 없는 것으로 읽힌다(포트는 열려 있는데 건강 확인만 실패하는 최악의 꼴이다)."""
        return [f"http://{'[' + host + ']' if ':' in host else host}:{self.port}{self.health_path}"
                for host in PROBE_HOSTS]

    def alive(self) -> bool:
        """지금 살아 있나 — **포트가 열려 있고 그 자리가 답해야** 살아 있다.

        답이 늦는 것과 먹통은 한 번으로 가를 수 없어, 이어서 몇 번 실패해야 `꺼짐`으로 본다. 측정이
        도는 중이면(마지막 답이 그렇게 말했으면) 더 참는다."""
        if not port_open(self.port):
            self.health_fails = 0  # 포트가 닫힌 것은 건강 문제가 아니라 꺼진 것이다
            return False
        ok, body = next(((ok, body) for ok, body in
                         (health_ok(url, self.health_marker) for url in self.health_urls()) if ok),
                        (False, {}))
        if ok:
            self.health_fails = 0
            self.busy = bool(body.get("busy"))
            return True
        self.health_fails += 1
        limit = HEALTH_FAILS_WHEN_BUSY if self.busy else HEALTH_FAILS_BEFORE_DOWN
        if self.health_fails < limit:
            return True  # 아직 참는 중 — 살아 있는 것으로 둔다
        self.last_error = f"{self.health_fails}번 이어서 답이 없다 ({self.health_path})"
        return False

    def status(self) -> dict:
        return {"name": self.name, "port": self.port, "up": port_open(self.port),
                "health": self.health_path, "health_fails": self.health_fails, "busy": self.busy,
                "ours": self.child_running(), "adopted": self.adopted, "restarts": self.restarts,
                "failures": self.failures, "last_action": self.last_action, "last_error": self.last_error}

    def spawn(self) -> None:
        try:
            # 창을 따로 만들지 않는다(감시 로그에 섞여 보이는 편이 낫다). Windows는 프로세스 그룹을
            # 따로 잡아야 npm이 낳은 node까지 함께 끌 수 있다
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
            self.child = subprocess.Popen(self.cmd, cwd=self.cwd, creationflags=flags)
            self.started_at = time.monotonic()
            self.last_error = None
            log(self.name, f"띄웠다 (pid {self.child.pid})")
        except OSError as exc:  # 실행 자체가 안 된 경우 — 까닭을 남기고 물러설 횟수에 센다
            self.failures += 1
            self.last_error = str(exc)
            log(self.name, f"띄우지 못했다: {exc}")

    def kill(self) -> None:
        child, self.child = self.child, None
        if child is None or child.poll() is not None:
            return
        if sys.platform == "win32":
            # npm.cmd가 node를 낳으므로 트리째 끈다 — 부모만 끄면 포트를 쥔 node가 남는다
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(child.pid)], capture_output=True, check=False)
        else:
            child.terminate()


@dataclass
class Watcher:
    services: list[Service]
    stopped: threading.Event = field(default_factory=threading.Event)

    def tick(self, service: Service) -> str:
        action = decide(is_open=service.alive(), child_running=service.child_running(),
                        since_start=time.monotonic() - service.started_at, failures=service.failures)
        if action == OK:
            service.last_error = None
            if service.child is None and not service.adopted:
                service.adopted = True  # 사람이 띄운 것을 그대로 둔다
                log(service.name, f"이미 떠 있다 (포트 {service.port}) — 그대로 둔다")
            service.failures = 0
        elif action == RESTART:
            if service.child_running():  # 떠 있는데 포트를 못 열었다 — 끄고 다시 띄운다
                service.kill()
            first = service.started_at == 0.0 and not service.adopted
            if not first:
                service.failures += 1
                service.restarts += 1
                log(service.name, f"꺼져 있다 — {backoff_sec(service.failures):.0f}초 뒤 다시 띄운다"
                                  f" (지금까지 {service.restarts}번)")
                self.stopped.wait(backoff_sec(service.failures))
            service.adopted = False
            if not self.stopped.is_set():
                service.spawn()
        elif action == GIVE_UP and service.last_action != GIVE_UP:
            log(service.name, f"{service.failures}번 이어서 살리지 못해 그만둔다 — 직접 띄워 까닭을 보라"
                              f" ({' '.join(service.cmd)})")
        service.last_action = action
        return action

    def loop(self) -> None:
        while not self.stopped.is_set():
            for service in self.services:
                if self.stopped.is_set():
                    break
                try:
                    self.tick(service)
                except Exception as exc:  # noqa: BLE001 — 감시가 죽으면 감시가 아니다
                    service.last_error = str(exc)
                    log(service.name, f"감시 중 오류: {exc}")
            self.stopped.wait(WATCH_SEC)

    def stop(self) -> None:
        self.stopped.set()
        for service in self.services:
            service.kill()


def services(root: Path = ROOT) -> list[Service]:
    """감시 대상 — 실행 방법은 `.claude/launch.json`의 것과 같게 둔다(두 벌이 되면 갈라진다)."""
    python = root / "backend" / ".venv" / "Scripts" / ("python.exe" if sys.platform == "win32" else "python")
    npm = shutil.which("npm") or "npm"
    return [
        # 건강 확인 자리 — 백엔드는 아무 일도 안 하는 답, 프런트는 개발 서버가 내주는 정적 파일이다
        Service("backend", 8000, [str(python), "-m", "uvicorn", "main:app", "--port", "8000"], root / "backend",
                health_path="/api/health", health_marker='"status"'),
        Service("frontend", 5173, [npm, "run", "dev"], root / "frontend",
                health_path="/health.json", health_marker='"service"'),
    ]


def status_server(watcher: Watcher, port: int) -> HTTPServer | None:
    """감시가 무엇을 하고 있는지 내보인다 — 볼 수 없는 감시는 믿을 수 없다."""
    if not port:
        return None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server의 이름 규칙
            body = json.dumps({"services": [s.status() for s in watcher.services],
                               "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                              ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # 상태 조회까지 찍으면 감시 로그가 안 보인다
            return

    # **묶어 보기 전에 먼저 두드린다.** Windows에서는 `allow_reuse_address` 때문에 이미 듣고 있는 포트에
    # 한 번 더 묶는 것이 성공한다 — 그러면 감시가 둘이 되어, 서로 띄운 서버를 남의 것으로 보고 번갈아 끄고
    # 띄운다(실제로 그렇게 됐다). 포트가 열려 있으면 이미 감시가 돌고 있는 것이다
    if port_open(port):
        raise AlreadyWatching(port)
    try:
        server = HTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        raise AlreadyWatching(port) from None
    threading.Thread(target=server.serve_forever, name="watch-status", daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="개발용 감시 서버 — 꺼진 쪽을 다시 띄운다")
    parser.add_argument("--only", choices=["backend", "frontend"], help="한쪽만 감시한다")
    parser.add_argument("--status-port", type=int, default=STATUS_PORT, help="상태 JSON 포트(0이면 안 연다)")
    args = parser.parse_args(argv)
    use_utf8_console()

    picked = [s for s in services() if args.only is None or s.name == args.only]
    watcher = Watcher(picked)
    try:
        server = status_server(watcher, args.status_port)
    except AlreadyWatching as exc:
        log("watch", f"이미 감시가 돌고 있다 (상태 포트 {exc.args[0]}) — 새로 띄우지 않는다")
        return 0
    log("watch", f"감시 시작 — {', '.join(f'{s.name}:{s.port}' for s in picked)}"
                 + (f" · 상태 http://127.0.0.1:{args.status_port}/" if server else ""))

    def bye(*_args):
        log("watch", "멈춘다 — 이 프로세스가 띄운 것만 끈다")
        watcher.stop()

    signal.signal(signal.SIGINT, bye)
    try:
        watcher.loop()
    finally:
        watcher.stop()
        if server is not None:
            server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
