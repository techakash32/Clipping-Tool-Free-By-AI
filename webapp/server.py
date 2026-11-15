"""
Podcast Shorts Factory — minimal web UI backend.

Wraps the existing `run.py` CLI with a small FastAPI app so the whole thing
can be driven from a browser: paste your API key + YouTube credentials,
paste a podcast URL, hit Run, watch it work.

Run it with:
    pip install fastapi uvicorn[standard] websockets
    python webapp/server.py

Then open http://127.0.0.1:8787
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent          # project root
STATIC = Path(__file__).resolve().parent / "static"
ENV_PATH = ROOT / ".env"
CONFIG_PATH = ROOT / "config.yaml"
TOKEN_PATH = ROOT / "youtube_token.json"
CLIENT_SECRET_PATH = ROOT / "client_secret.json"

app = FastAPI(title="Podcast Shorts Factory")

# ── env (.env) helpers ────────────────────────────────────────────────
ENV_FIELDS = [
    "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "NVIDIA_API_KEY",
    "GEMINI_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "PEXELS_API_KEY",
    "TIKTOK_ACCESS_TOKEN", "IG_ACCESS_TOKEN", "IG_USER_ID",
]
PROVIDER_KEY = {
    "anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY", "nvidia": "NVIDIA_API_KEY", "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY", "ollama": None,
}


def _read_env() -> dict:
    values = {k: "" for k in ENV_FIELDS}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.split("#", 1)[0].strip()  # drop inline comments like `# provider: groq (...)`
            if k in values and v and not v.endswith("..."):
                values[k] = v
    return values


def _write_env(updates: dict) -> None:
    """Merge `updates` into .env, preserving existing lines/comments."""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        k = stripped.split("=", 1)[0].strip()
        if k in updates:
            lines[i] = f"{k}={updates[k]}"
            seen.add(k)
    extra = [f"{k}={v}" for k, v in updates.items() if k not in seen]
    if extra:
        if lines:
            lines.append("")
        lines.append("# ── added via web UI ──")
        lines.extend(extra)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_provider() -> str:
    if not CONFIG_PATH.exists():
        return "anthropic"
    m = re.search(r"(?m)^\s*provider:\s*(\S+)", CONFIG_PATH.read_text(encoding="utf-8"))
    return m.group(1) if m else "anthropic"


def _write_provider(provider: str) -> None:
    text = CONFIG_PATH.read_text(encoding="utf-8")
    new_text, n = re.subn(r"(?m)^(\s*provider:\s*)\S+", rf"\g<1>{provider}", text, count=1)
    if n:
        CONFIG_PATH.write_text(new_text, encoding="utf-8")


# ── credentials endpoints ───────────────────────────────────────────
@app.get("/api/credentials")
def get_credentials():
    env = _read_env()
    masked = {k: (("•" * 6 + v[-4:]) if v else "") for k, v in env.items()}
    set_flags = {k: bool(v) for k, v in env.items()}
    return {
        "provider": _read_provider(),
        "values": masked,
        "set": set_flags,
        "youtube_secret_uploaded": CLIENT_SECRET_PATH.exists(),
        "youtube_connected": TOKEN_PATH.exists(),
    }


@app.post("/api/credentials")
async def save_credentials(payload: dict):
    provider = payload.get("provider")
    keys = payload.get("keys", {})
    updates = {k: v for k, v in keys.items() if k in ENV_FIELDS and v is not None and v != ""}
    if updates:
        _write_env(updates)
    if provider:
        _write_provider(provider)
    return {"ok": True}


@app.post("/api/youtube/secret")
async def upload_youtube_secret(file: UploadFile = File(...)):
    data = await file.read()
    try:
        json.loads(data)
    except Exception:
        return JSONResponse({"ok": False, "error": "That doesn't look like a valid JSON file."}, status_code=400)
    CLIENT_SECRET_PATH.write_bytes(data)
    _write_env({"YOUTUBE_CLIENT_SECRETS": "client_secret.json"})
    return {"ok": True}


@app.get("/api/youtube/status")
def youtube_status():
    return {
        "secret_uploaded": CLIENT_SECRET_PATH.exists(),
        "connected": TOKEN_PATH.exists(),
        "channel": _state.get("youtube_channel"),
    }


@app.post("/api/youtube/connect")
def youtube_connect():
    """Runs `python run.py auth-youtube`. This opens a browser window ON THE
    MACHINE RUNNING THIS SERVER (it's a local OAuth loopback flow) — that's
    expected when you're running this on your own computer."""
    if not CLIENT_SECRET_PATH.exists():
        return JSONResponse({"ok": False, "error": "Upload client_secret.json first."}, status_code=400)
    try:
        proc = subprocess.run(
            [sys.executable, "-u", "run.py", "auth-youtube"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return JSONResponse({"ok": False, "error": "Timed out waiting for the Google sign-in to finish."}, status_code=504)
    out = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"connected as:\s*(.+)", out)
    if m:
        _state["youtube_channel"] = _clean(m.group(1)).strip()
        return {"ok": True, "channel": _state["youtube_channel"]}
    return JSONResponse({"ok": False, "error": _clean(out)[-800:] or "Authentication failed."}, status_code=400)


@app.post("/api/youtube/disconnect")
def youtube_disconnect():
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()
    _state["youtube_channel"] = None
    return {"ok": True}


# ── pipeline run state ──────────────────────────────────────────────
STAGES = ["Trend Scout", "Pick source video", "Find", "Review", "Edit",
          "Finishing review", "Upload", "Stats"]
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _clean(text: str) -> str:
    return ANSI_RE.sub("", text)


_state = {
    "status": "idle",       # idle | running | done | error
    "stage": None,
    "logs": [],              # list[str]
    "url": None,
    "youtube_channel": None,
    "last_log_at": None,     # epoch seconds of the last log line received
}
_lock = threading.Lock()
_ws_clients: list[WebSocket] = []
_loop: asyncio.AbstractEventLoop | None = None


def _broadcast(line: str):
    if _loop is None:
        return
    for ws in list(_ws_clients):
        try:
            asyncio.run_coroutine_threadsafe(ws.send_text(line), _loop)
        except Exception:
            pass


def _append_log(line: str):
    with _lock:
        _state["logs"].append(line)
        _state["logs"] = _state["logs"][-500:]
        _state["last_log_at"] = time.time()
        for s in STAGES:
            if s.lower() in line.lower():
                _state["stage"] = s
                break
    _broadcast(line)


def _run_pipeline(mode: str, url: str, auto_yes: bool):
    with _lock:
        _state["status"] = "running"
        _state["stage"] = None
        _state["logs"] = []
        _state["url"] = url
    args = [sys.executable, "-u", "run.py", mode]
    if mode == "auto":
        args.append(url)
    if auto_yes:
        args.append("--yes")
    env = {**__import__("os").environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.Popen(
            args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace", env=env,
        )
        with _lock:
            _state["pid"] = proc.pid
        for raw in proc.stdout:  # type: ignore[union-attr]
            _append_log(_clean(raw.rstrip("\n")))
        code = proc.wait()
        with _lock:
            _state["status"] = "done" if code == 0 else "error"
    except FileNotFoundError as e:
        _append_log(f"error: {e}")
        with _lock:
            _state["status"] = "error"


@app.post("/api/run")
def run_pipeline(payload: dict):
    with _lock:
        if _state["status"] == "running":
            return JSONResponse({"ok": False, "error": "A run is already in progress."}, status_code=409)
    mode = payload.get("mode", "auto")
    url = (payload.get("url") or "").strip()
    auto_yes = bool(payload.get("auto_yes", True))
    if mode == "auto" and not url:
        return JSONResponse({"ok": False, "error": "Paste a podcast URL first."}, status_code=400)
    threading.Thread(target=_run_pipeline, args=(mode, url, auto_yes), daemon=True).start()
    return {"ok": True}


@app.get("/api/status")
def get_status():
    with _lock:
        idle_for = (time.time() - _state["last_log_at"]) if _state["last_log_at"] else None
        return {
            "status": _state["status"],
            "stage": _state["stage"],
            "stages": STAGES,
            "url": _state["url"],
            "logs": _state["logs"][-200:],
            "seconds_since_last_log": round(idle_for) if idle_for is not None else None,
        }


@app.websocket("/api/ws/logs")
async def ws_logs(ws: WebSocket):
    global _loop
    _loop = asyncio.get_event_loop()
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


# ── history ──────────────────────────────────────────────────────────
@app.get("/api/history")
def get_history():
    try:
        from factory import db
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"factory package not importable: {e}"}, status_code=500)
    out = []
    try:
        with db.conn() as c:
            rows = c.execute(
                "SELECT clips.id, clips.title, clips.score, clips.status, "
                "clips.rendered_path, clips.created_at, sources.title as source_title "
                "FROM clips LEFT JOIN sources ON clips.source_id = sources.id "
                "ORDER BY clips.id DESC LIMIT 100"
            ).fetchall()
            for r in rows:
                out.append(dict(r))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    return {"ok": True, "clips": out}


# ── static frontend ──────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)
