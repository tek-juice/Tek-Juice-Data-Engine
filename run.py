#!/usr/bin/env python3
"""
DATA ENGINE — Dev Runner
========================
One command to start any service and open its Swagger UI in the browser.

Usage
------
    python run.py                     # shows the interactive menu
    python run.py seo                 # start SEO engine → http://localhost:8012/docs
    python run.py aeo                 # start AEO engine → http://localhost:8014/docs
    python run.py geo                 # start GEO engine → http://localhost:8013/docs
    python run.py docs                # start unified docs server → http://localhost:8099/docs
    python run.py all                 # start ALL services (one process each, non-blocking)

Available service keys
-----------------------
    gateway     api_gateway          :8000
    ingest      ingestion_service    :8001
    chunk       chunking_service     :8002
    embed       embedding_service    :8003
    vectors     vector_vault         :8004
    telemetry   telemetry_service    :8005
    scraper     trend_scraper        :8006
    semantic    semantic_engine      :8007
    gaps        gap_detection        :8008
    schema      schema_factory       :8009
    sync        synchronization      :8010
    dashboard   dashboard_backend    :8011
    seo         seo_engine           :8012
    geo         geo_engine           :8013
    aeo         aeo_engine           :8014
    docs        swagger_server       :8099  (unified Swagger UI)
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# ── Service registry ──────────────────────────────────────────────────────────

SERVICES: dict[str, dict] = {
    "gateway":   {"module": "services.api_gateway.gateway:app",           "port": 8000, "label": "API Gateway"},
    "ingest":    {"module": "services.ingestion_service.main:app",        "port": 8001, "label": "Ingestion Service"},
    "chunk":     {"module": "services.chunking_service.main:app",         "port": 8002, "label": "Chunking Service"},
    "embed":     {"module": "services.embedding_service.main:app",        "port": 8003, "label": "Embedding Service"},
    "vectors":   {"module": "services.vector_vault.main:app",             "port": 8004, "label": "Vector Vault"},
    "telemetry": {"module": "services.telemetry_service.main:app",        "port": 8005, "label": "Telemetry Service"},
    "scraper":   {"module": "services.trend_scraper.main:app",            "port": 8006, "label": "Trend Scraper"},
    "semantic":  {"module": "services.semantic_engine.main:app",          "port": 8007, "label": "Semantic Engine"},
    "gaps":      {"module": "services.gap_detection.main:app",            "port": 8008, "label": "Gap Detection"},
    "schema":    {"module": "services.schema_factory.main:app",           "port": 8009, "label": "Schema Factory"},
    "sync":      {"module": "services.synchronization.main:app",          "port": 8010, "label": "Synchronization"},
    "dashboard": {"module": "services.dashboard_backend.main:app",        "port": 8011, "label": "Dashboard Backend"},
    "seo":       {"module": "services.seo_engine.main:app",               "port": 8012, "label": "SEO Engine"},
    "geo":       {"module": "services.geo_engine.main:app",               "port": 8013, "label": "GEO Engine"},
    "aeo":       {"module": "services.aeo_engine.main:app",               "port": 8014, "label": "AEO Engine"},
    "docs":      {"module": "docs.swagger_server:app",                    "port": 8099, "label": "Unified Docs Server"},
}

# Aliases so you can type the full name too
ALIASES: dict[str, str] = {
    "api_gateway":        "gateway",
    "ingestion":          "ingest",
    "ingestion_service":  "ingest",
    "chunking":           "chunk",
    "chunking_service":   "chunk",
    "embedding":          "embed",
    "embedding_service":  "embed",
    "vector_vault":       "vectors",
    "trend_scraper":      "scraper",
    "semantic_engine":    "semantic",
    "gap_detection":      "gaps",
    "schema_factory":     "schema",
    "synchronization":    "sync",
    "dashboard_backend":  "dashboard",
    "seo_engine":         "seo",
    "geo_engine":         "geo",
    "aeo_engine":         "aeo",
}

ROOT = Path(__file__).parent
PYTHON = str(ROOT / ".venv" / "bin" / "python")

# Always run from the project root so .env and .env.local are found correctly
os.chdir(ROOT)

# ── .env.local loader ────────────────────────────────────────────────────────
# When a .env.local file exists (local dev, outside Docker), its values are
# injected into os.environ so that uvicorn subprocesses inherit them.
# This lets .env keep Docker hostnames (postgres, pgbouncer, redis) while
# .env.local overrides them to localhost for host-side development.

def _load_env_local() -> None:
    """Inject .env.local into os.environ so uvicorn subprocesses inherit it.

    Uses direct assignment (not setdefault) so .env.local values always
    override whatever the parent shell may have exported — this is the whole
    point of the file (localhost overrides for local dev).
    """
    env_local = ROOT / ".env.local"
    if not env_local.exists():
        return
    with env_local.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ[key.strip()] = value.strip()


_load_env_local()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve(key: str) -> str | None:
    key = key.strip().lower()
    if key in SERVICES:
        return key
    return ALIASES.get(key)


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _pids_on_port(port: int) -> list[int]:
    """Return PIDs of processes listening on the given port (macOS/Linux)."""
    try:
        import subprocess as _sp
        out = _sp.check_output(
            ["lsof", "-ti", f":{port}"], text=True, stderr=_sp.DEVNULL
        )
        return [int(p) for p in out.split() if p.strip().isdigit()]
    except Exception:
        return []


def _start_service(key: str, open_browser: bool = True) -> None:
    svc    = SERVICES[key]
    port   = svc["port"]
    label  = svc["label"]
    url    = f"http://localhost:{port}/docs"

    if _port_in_use(port):
        pids = _pids_on_port(port)
        print(f"\n  ✗  Port {port} is already in use", end="")
        if pids:
            print(f" (PID {', '.join(str(p) for p in pids)}).")
        else:
            print(".")

        # If it's a local Python/uvicorn process offer to kill it automatically
        if pids:
            answer = input("     Kill the stale process and continue? [Y/n] ").strip().lower()
            if answer in ("", "y", "yes"):
                import signal
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                time.sleep(1)
                if _port_in_use(port):
                    for pid in pids:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    time.sleep(0.5)
                print(f"     Killed. Starting {label}…")
            else:
                print(f"     If a Docker container is using it, open {url} directly,")
                print(f"     or stop it:  docker compose stop {key}\n")
                sys.exit(1)
        else:
            print(f"     If a Docker container is using it, open {url} directly,")
            print(f"     or stop it:  docker compose stop {key}\n")
            sys.exit(1)

    print(f"\n  ⚡ Starting {label} on port {port}")
    print(f"     Swagger UI → {url}")
    print("     Press Ctrl+C to stop\n")

    if open_browser:
        # Give uvicorn ~1.5 s to bind before opening the browser
        def _open():
            time.sleep(1.5)
            webbrowser.open(url)

        import threading
        threading.Thread(target=_open, daemon=True).start()

    # Run uvicorn — this blocks until Ctrl+C
    subprocess.run([
        PYTHON, "-m", "uvicorn",
        svc["module"],
        "--host", "0.0.0.0",
        "--port", str(port),
        "--reload",
        "--reload-dir", str(ROOT),
    ], cwd=str(ROOT))


def _start_all() -> None:
    """Start all services as background subprocesses, then block."""
    procs = []
    print("\n  ⚡ Starting all DATA ENGINE services…\n")

    for key, svc in SERVICES.items():
        if key == "docs":
            continue  # skip unified docs server in 'all' mode
        port  = svc["port"]
        label = svc["label"]
        print(f"     ▸ {label:<30} → http://localhost:{port}/docs")
        p = subprocess.Popen([
            PYTHON, "-m", "uvicorn",
            svc["module"],
            "--host", "0.0.0.0",
            "--port", str(port),
        ], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(p)

    time.sleep(2)
    print("\n  Starting unified docs server on :8099…")
    docs = SERVICES["docs"]
    procs.append(subprocess.Popen([
        PYTHON, "-m", "uvicorn",
        docs["module"],
        "--host", "0.0.0.0",
        "--port", str(docs["port"]),
    ], cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))

    time.sleep(1.5)
    print("\n  All services started. Opening unified docs…")
    webbrowser.open("http://localhost:8099/docs")
    print("  Press Ctrl+C to stop all services.\n")

    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        print("\n  Stopping all services…")
        for p in procs:
            p.terminate()
        print("  Done.")


def _menu() -> str:
    """Interactive service picker."""
    print("\n" + "─" * 55)
    print("  ⚡  DATA ENGINE — Dev Runner")
    print("─" * 55)
    items = list(SERVICES.items())
    for i, (key, svc) in enumerate(items, 1):
        print(f"  {i:>2}.  {svc['label']:<30}  :{svc['port']}")
    print(f"  {len(items)+1:>2}.  All services (background)")
    print("─" * 55)

    while True:
        choice = input("  Pick a number (or type a service key): ").strip()
        if choice.isdigit():
            n = int(choice)
            if 1 <= n <= len(items):
                return items[n - 1][0]
            if n == len(items) + 1:
                return "all"
        else:
            resolved = _resolve(choice)
            if resolved:
                return resolved
            print(f"  Unknown service '{choice}'. Try again.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if not args:
        key = _menu()
    else:
        key = args[0].lower()

    if key == "all":
        _start_all()
        return

    resolved = _resolve(key)
    if not resolved:
        print(f"\n  ✗  Unknown service: '{key}'")
        print(f"     Valid keys: {', '.join(SERVICES)}")
        sys.exit(1)

    _start_service(resolved, open_browser=True)


if __name__ == "__main__":
    main()
