"""FastAPI backend for OpendataUA Web UI."""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from collections import deque
from copy import deepcopy
from pathlib import Path
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

REPO_ROOT = Path(__file__).parent.parent
CONFIG_PATH = REPO_ROOT / "data" / "config.json"
STATIC_DIR = Path(__file__).parent / "static"
LOG_PATH = REPO_ROOT / "data" / "server.log"

DEFAULT_CONFIG: dict = {
    "openData": {
        "address": "https://opendata.cwa.gov.tw/api/v1/rest/datastore/",
        "api": "O-A0003-001",
        "auth_key": "",
        "stations": [],
    },
    "opcUA": {
        "url": "opc.tcp://0.0.0.0:48484",
        "bind_ip": "",
    },
}

_server_proc: subprocess.Popen | None = None
_server_lock = threading.Lock()
_log_buffer: deque[str] = deque(maxlen=1000)
_log_lock = threading.Lock()
_log_subscribers: list[asyncio.Queue] = []
_loop: asyncio.AbstractEventLoop | None = None

app = FastAPI(title="OpendataUA Web UI", docs_url=None, redoc_url=None)
_OPCUA_PORT = 48484


def _add_log(line: str) -> None:
    line = line.rstrip()
    if not line:
        return
    with _log_lock:
        _log_buffer.append(line)
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
    if _loop and not _loop.is_closed():
        for q in list(_log_subscribers):
            try:
                _loop.call_soon_threadsafe(q.put_nowait, line)
            except Exception:
                pass


def _log_file_tail(limit: int = 1000) -> list[str]:
    try:
        if not LOG_PATH.exists():
            return []
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            return f.read().splitlines()[-limit:]
    except Exception:
        return []


def _read_proc_stream(stream, prefix: str = "") -> None:
    if stream is None:
        return
    try:
        for raw in iter(stream.readline, b""):
            text = raw.decode("utf-8", errors="replace").rstrip()
            if text:
                _add_log(f"{prefix}{text}")
    except Exception:
        pass


def _port_pid(port: int) -> int | None:
    """Return the PID listening on a TCP port, or None when not found."""
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f":{port}"], stderr=subprocess.DEVNULL
        ).decode().strip()
        for tok in out.splitlines():
            tok = tok.strip()
            if tok.isdigit():
                return int(tok)
    except Exception:
        pass
    return None


def _proc_alive(proc: subprocess.Popen | None) -> bool:
    return proc is not None and proc.poll() is None


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Return True when a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False



def _launch_server_process() -> dict:
    """Launch the OPC UA server subprocess. Caller must hold _server_lock."""
    global _server_proc
    cmd = [sys.executable, str(REPO_ROOT / "main.py"), "server", "--config", str(CONFIG_PATH)]
    _add_log(f"[SYS] Starting OPC UA Server: {' '.join(cmd)}")
    _server_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO_ROOT),
        env=os.environ.copy(),
    )
    pid = _server_proc.pid
    _add_log(f"[SYS] Server started (PID {pid})")
    threading.Thread(target=_read_proc_stream, args=(_server_proc.stdout, ""), daemon=True).start()
    threading.Thread(target=_read_proc_stream, args=(_server_proc.stderr, "[ERR] "), daemon=True).start()
    return {"status": "started", "pid": pid}


async def _server_watchdog() -> None:
    """Keep the OPC UA server running while the Web UI container is alive."""
    await asyncio.sleep(3)
    while True:
        try:
            with _server_lock:
                if not _proc_alive(_server_proc) and not _port_open(_OPCUA_PORT):
                    _add_log("[WATCHDOG] OPC UA Server is not running; restarting")
                    try:
                        _launch_server_process()
                    except Exception as e:
                        _add_log(f"[WATCHDOG] Restart failed: {e}")
        except Exception as e:
            _add_log(f"[WATCHDOG] Check failed: {e}")
        await asyncio.sleep(10)


@app.on_event("startup")
async def _startup_watchdog() -> None:
    asyncio.create_task(_server_watchdog())

@app.get("/api/config")
def api_get_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return deepcopy(DEFAULT_CONFIG)


@app.put("/api/config")
def api_save_config(body: dict) -> dict:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=4)
    return {"status": "ok"}


@app.get("/api/server/status")
def api_server_status() -> dict:
    global _server_proc
    with _server_lock:
        if _proc_alive(_server_proc):
            return {"running": True, "pid": _server_proc.pid}

        orphan_pid = _port_pid(_OPCUA_PORT)
        if orphan_pid:
            _server_proc = None
            return {"running": True, "pid": orphan_pid, "orphan": True}

        if _port_open(_OPCUA_PORT):
            _server_proc = None
            return {"running": True, "pid": None, "orphan": True, "port": _OPCUA_PORT}

        return {"running": False, "pid": None}


@app.post("/api/server/start")
def api_server_start() -> dict:
    global _server_proc
    with _server_lock:
        if _proc_alive(_server_proc):
            return {"status": "already_running", "pid": _server_proc.pid}

        orphan_pid = _port_pid(_OPCUA_PORT)
        if orphan_pid:
            return {"status": "already_running", "pid": orphan_pid, "orphan": True}
        if _port_open(_OPCUA_PORT):
            return {"status": "already_running", "pid": None, "orphan": True, "port": _OPCUA_PORT}

        try:
            return _launch_server_process()
        except Exception as e:
            _add_log(f"[ERR] Failed to start server: {e}")
            raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/server/stop")
def api_server_stop() -> dict:
    global _server_proc
    with _server_lock:
        target_pid: int | None = None
        if _proc_alive(_server_proc):
            target_pid = _server_proc.pid
        else:
            target_pid = _port_pid(_OPCUA_PORT)

        if target_pid is None:
            if _port_open(_OPCUA_PORT):
                return {"status": "running_external", "message": "OPC UA port is open, but PID is unavailable because lsof is not installed."}
            return {"status": "not_running"}

        _add_log(f"[SYS] Stopping OPC UA Server (PID {target_pid})")
        try:
            os.kill(target_pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

        if _proc_alive(_server_proc):
            try:
                _server_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _server_proc.kill()
                _server_proc.wait()

        _server_proc = None
        _add_log("[SYS] Server stopped")
        return {"status": "stopped"}


@app.get("/api/logs")
async def api_logs(request: Request) -> StreamingResponse:
    global _loop
    _loop = asyncio.get_running_loop()

    async def gen() -> AsyncGenerator[str, None]:
        with _log_lock:
            history = _log_file_tail() + list(_log_buffer)
        seen: set[str] = set()
        for line in history[-1000:]:
            if line in seen:
                continue
            seen.add(line)
            yield f"data: {line}\n\n"

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        _log_subscribers.append(queue)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=20.0)
                    yield f"data: {line}\n\n"
                except asyncio.TimeoutError:
                    yield "data: \n\n"
        finally:
            if queue in _log_subscribers:
                _log_subscribers.remove(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/weather")
def api_weather() -> list:
    if not CONFIG_PATH.exists():
        raise HTTPException(status_code=400, detail="data/config.json does not exist")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)

    od = cfg.get("openData", {})
    addr = od.get("address", "").strip()
    api = od.get("api", "").strip()
    auth = od.get("auth_key", "").strip()
    stations = od.get("stations", [])
    ids = [s["id"].strip() for s in stations if isinstance(s, dict) and s.get("id")]

    if not (addr and api and auth and ids):
        raise HTTPException(
            status_code=400,
            detail="Missing required config: address / api / auth_key / stations",
        )

    url = (
        f"{addr}{api}"
        f"?Authorization={auth}"
        f"&format=JSON"
        f"&StationId={','.join(ids)}"
        f"&WeatherElement=&GeoInfo=StationAltitude,CountyName"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.load(resp)
        return data.get("records", {}).get("Station", [])
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"OpenData API error: {e.code} {e.reason}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/health")
def api_health() -> dict:
    return {"status": "ok"}


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:
    @app.get("/")
    def root() -> HTMLResponse:
        return HTMLResponse("<h2>webui/static is missing</h2>")


def run(host: str = "0.0.0.0", port: int = 8188) -> None:
    print(f"[Web UI] Starting on http://{host}:{port}", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()