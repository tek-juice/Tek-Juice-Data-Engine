"""
DATA ENGINE — Unified Swagger / OpenAPI Docs Server
====================================================
Runs on port 8099.  Fetches every service's native FastAPI /openapi.json,
merges them into one spec, and serves:

    http://localhost:8099/docs          → Swagger UI  (unified)
    http://localhost:8099/redoc         → ReDoc        (unified)
    http://localhost:8099/openapi.json  → merged raw spec (JSON)
    http://localhost:8099/openapi.yaml  → merged raw spec (YAML, for Postman)
    http://localhost:8099/services      → list of registered services + status
    http://localhost:8099/services/{name}/docs  → Swagger UI scoped to ONE service

How it connects to your services
---------------------------------
Each FastAPI service exposes /openapi.json automatically — no extra code needed.
This server fetches those endpoints at startup and at every /refresh call,
then stitches them together into a single spec that Swagger UI can render.

Usage
-----
    # Start the docs server (services must already be running)
    python docs/swagger_server.py

    # Or with uvicorn directly:
    uvicorn docs.swagger_server:app --port 8099 --reload
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from datetime import datetime, UTC
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

# ── Service registry ──────────────────────────────────────────────────────────
# Maps a friendly name → base URL.  Adjust ports if you changed defaults.

SERVICES: dict[str, dict[str, str]] = {
    "api_gateway": {
        "url":         "http://localhost:8000",
        "label":       "API Gateway",
        "description": "Central entry point — auth, rate limiting, routing",
        "color":       "#6366f1",
    },
    "ingestion": {
        "url":         "http://localhost:8001",
        "label":       "Ingestion Service",
        "description": "Document upload, preprocessing, pipeline dispatch",
        "color":       "#0ea5e9",
    },
    "chunking": {
        "url":         "http://localhost:8002",
        "label":       "Chunking Service",
        "description": "Token and semantic text chunking",
        "color":       "#06b6d4",
    },
    "embedding": {
        "url":         "http://localhost:8003",
        "label":       "Embedding Service",
        "description": "Gemini / OpenAI / Voyage / Jina vector embeddings",
        "color":       "#8b5cf6",
    },
    "vector_vault": {
        "url":         "http://localhost:8004",
        "label":       "Vector Vault",
        "description": "pgvector storage and HNSW similarity search",
        "color":       "#ec4899",
    },
    "trend_scraper": {
        "url":         "http://localhost:8006",
        "label":       "Trend Scraper",
        "description": "Google, Bing, News + 9 social media platforms",
        "color":       "#f59e0b",
    },
    "semantic_engine": {
        "url":         "http://localhost:8007",
        "label":       "Semantic Engine",
        "description": "Cosine similarity, re-ranking, clustering",
        "color":       "#10b981",
    },
    "gap_detection": {
        "url":         "http://localhost:8008",
        "label":       "Gap Detection + Quality Score",
        "description": "Content gap analysis, LLM writing agent, Google Ads Quality Score / Ad Rank engine",
        "color":       "#ef4444",
    },
    "schema_factory": {
        "url":         "http://localhost:8009",
        "label":       "Schema Factory",
        "description": "GEO-optimised Schema.org JSON-LD generation",
        "color":       "#f97316",
    },
    "synchronization": {
        "url":         "http://localhost:8010",
        "label":       "Synchronization",
        "description": "Cache invalidation, data pool sync, replication",
        "color":       "#64748b",
    },
    "dashboard_backend": {
        "url":         "http://localhost:8011",
        "label":       "Dashboard Backend",
        "description": "Analytics, metrics, WebSocket activity feed",
        "color":       "#0891b2",
    },
    "seo_engine": {
        "url":         "http://localhost:8012",
        "label":       "SEO Engine",
        "description": "On-page SEO, rank tracking, domain authority",
        "color":       "#16a34a",
    },
    "geo_engine": {
        "url":         "http://localhost:8013",
        "label":       "GEO Engine",
        "description": "GEO + LEO + VSEO — entity extraction, LLM visibility",
        "color":       "#7c3aed",
    },
    "aeo_engine": {
        "url":         "http://localhost:8014",
        "label":       "AEO Engine",
        "description": "Answer boxes, featured snippets, voice search, position-zero",
        "color":       "#db2777",
    },
}

# ── Spec cache ────────────────────────────────────────────────────────────────

_spec_cache: dict[str, Any] = {}          # name → openapi dict
_fetch_status: dict[str, str] = {}        # name → "ok" | "unreachable" | "error"
_last_refresh: datetime | None = None

log = logging.getLogger("swagger_server")
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")


# ── Fetch helpers ─────────────────────────────────────────────────────────────

async def _fetch_one(name: str, base_url: str) -> None:
    """Fetch /openapi.json from a single service and cache it."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{base_url}/openapi.json")
            resp.raise_for_status()
            _spec_cache[name] = resp.json()
            _fetch_status[name] = "ok"
            log.info("  ✓  %-22s  %s", name, base_url)
    except httpx.ConnectError:
        _fetch_status[name] = "unreachable"
        log.warning("  ✗  %-22s  unreachable", name)
    except Exception as exc:
        _fetch_status[name] = f"error: {exc}"
        log.warning("  ✗  %-22s  %s", name, exc)


async def _fetch_all() -> None:
    """Fetch all service specs concurrently."""
    global _last_refresh
    log.info("Fetching OpenAPI specs from %d services…", len(SERVICES))
    await asyncio.gather(*[
        _fetch_one(name, meta["url"])
        for name, meta in SERVICES.items()
    ])
    _last_refresh = datetime.now(UTC)
    log.info("Done — %d/%d specs loaded", sum(1 for s in _fetch_status.values() if s == "ok"), len(SERVICES))


# ── Spec merger ───────────────────────────────────────────────────────────────

def _merge_specs() -> dict[str, Any]:
    """
    Merge all cached per-service specs into one unified OpenAPI 3.1 spec.

    Strategy:
    - paths: prefixed with /api/v1 where missing, then merged into one dict.
      If two services declare the same path, the later one wins (logged as warning).
    - components/schemas: namespaced with ServiceName_ prefix to avoid collisions.
    - components/securitySchemes: merged once (all services share the same auth).
    - tags: deduplicated by name.
    - servers: one entry per service so Swagger UI lets you switch base URL.
    """
    merged: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title":       "DATA ENGINE — Unified API",
            "version":     "1.0.0",
            "description": _build_description(),
        },
        "servers":    _build_servers(),
        "tags":       [],
        "paths":      {},
        "components": {
            "securitySchemes": {},
            "schemas":         {},
        },
        "security": [{"BearerAuth": []}, {"ApiKeyAuth": []}],
    }

    seen_tags: set[str] = set()

    for svc_name, spec in _spec_cache.items():
        meta    = SERVICES[svc_name]
        ns      = _to_namespace(svc_name)        # e.g. "GeoEngine"
        prefix  = meta["url"]

        # ── tags ────────────────────────────────────────────────────────────
        for tag in spec.get("tags", []):
            if tag["name"] not in seen_tags:
                merged["tags"].append(tag)
                seen_tags.add(tag["name"])

        # ── components/schemas ──────────────────────────────────────────────
        for schema_name, schema_def in spec.get("components", {}).get("schemas", {}).items():
            namespaced = f"{ns}_{schema_name}"
            merged["components"]["schemas"][namespaced] = copy.deepcopy(schema_def)

        # ── components/securitySchemes ───────────────────────────────────────
        for ss_name, ss_def in spec.get("components", {}).get("securitySchemes", {}).items():
            merged["components"]["securitySchemes"].setdefault(ss_name, copy.deepcopy(ss_def))

        # ── paths ────────────────────────────────────────────────────────────
        for path, path_item in spec.get("paths", {}).items():
            if path in merged["paths"]:
                log.warning("Path collision: %s (from %s) — overwriting", path, svc_name)
            # Tag each operation with the service name so Swagger groups by service
            patched_item = copy.deepcopy(path_item)
            for method_obj in patched_item.values():
                if isinstance(method_obj, dict):
                    existing_tags = method_obj.get("tags", [])
                    if not existing_tags:
                        method_obj["tags"] = [meta["label"]]
                    # Rewrite local $ref paths to namespaced equivalents
                    _rewrite_refs(method_obj, ns)
            merged["paths"][path] = patched_item

    return merged


def _rewrite_refs(obj: Any, ns: str) -> None:
    """Recursively rewrite #/components/schemas/Foo → #/components/schemas/Ns_Foo."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if key == "$ref" and isinstance(val, str) and val.startswith("#/components/schemas/"):
                schema_name = val.split("/")[-1]
                obj[key] = f"#/components/schemas/{ns}_{schema_name}"
            else:
                _rewrite_refs(val, ns)
    elif isinstance(obj, list):
        for item in obj:
            _rewrite_refs(item, ns)


def _to_namespace(svc_name: str) -> str:
    """Convert snake_case service name to PascalCase namespace prefix."""
    return "".join(part.capitalize() for part in svc_name.split("_"))


def _build_description() -> str:
    lines = [
        "## DATA ENGINE — Unified API Reference",
        "",
        "All 14 microservices in one place.  Authenticate once, test everything.",
        "",
        "### Authentication",
        "- **Bearer JWT** — `POST /api/v1/auth/token` → use the `Authorize` button above",
        "- **API Key** — `POST /api/v1/auth/api-keys` → pass as `X-API-Key` header",
        "",
        "### Default Embedding Provider",
        "**Gemini** (`text-embedding-004`, 768d) — override per-request via the `provider` field.",
        "",
        "### Service Status",
    ]
    for name, meta in SERVICES.items():
        status = _fetch_status.get(name, "pending")
        icon   = "🟢" if status == "ok" else "🔴"
        lines.append(f"- {icon} **{meta['label']}** — {meta['url']} — {meta['description']}")
    if _last_refresh:
        lines += ["", f"_Last refreshed: {_last_refresh.isoformat()}_"]
    return "\n".join(lines)


def _build_servers() -> list[dict]:
    servers = [{"url": "http://localhost:8000", "description": "API Gateway (recommended)"}]
    for name, meta in SERVICES.items():
        if name != "api_gateway":
            servers.append({"url": meta["url"], "description": meta["label"]})
    return servers


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="DATA ENGINE — Docs Server",
    description="Internal docs server — serves the unified Swagger UI.",
    version="1.0.0",
    # Disable the built-in /docs since we serve our own enhanced version
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup() -> None:
    await _fetch_all()


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/openapi.json", include_in_schema=False)
async def get_openapi_json() -> JSONResponse:
    """Return the merged OpenAPI spec as JSON."""
    return JSONResponse(_merge_specs())


@app.get("/openapi.yaml", include_in_schema=False)
async def get_openapi_yaml() -> Response:
    """Return the merged OpenAPI spec as YAML (Postman-compatible)."""
    content = yaml.dump(_merge_specs(), allow_unicode=True, sort_keys=False)
    return Response(
        content=content,
        media_type="application/yaml",
        headers={"Content-Disposition": "attachment; filename=data_engine_openapi.yaml"},
    )


@app.get("/services", include_in_schema=False)
async def list_services() -> JSONResponse:
    """Return all registered services with their fetch status."""
    return JSONResponse({
        "last_refresh": _last_refresh.isoformat() if _last_refresh else None,
        "services": [
            {
                "name":        name,
                "label":       meta["label"],
                "url":         meta["url"],
                "description": meta["description"],
                "status":      _fetch_status.get(name, "pending"),
                "spec_loaded": name in _spec_cache,
            }
            for name, meta in SERVICES.items()
        ],
    })


@app.post("/refresh", include_in_schema=False)
async def refresh_specs() -> JSONResponse:
    """Re-fetch all service specs. Call this after deploying a service update."""
    _spec_cache.clear()
    _fetch_status.clear()
    await _fetch_all()
    ok    = sum(1 for s in _fetch_status.values() if s == "ok")
    total = len(SERVICES)
    return JSONResponse({"refreshed": ok, "total": total, "last_refresh": _last_refresh.isoformat()})


@app.get("/services/{service_name}/openapi.json", include_in_schema=False)
async def get_service_spec(service_name: str) -> JSONResponse:
    """Return the raw spec for a single service."""
    if service_name not in _spec_cache:
        raise HTTPException(status_code=404, detail=f"Spec for '{service_name}' not loaded or service unreachable.")
    return JSONResponse(_spec_cache[service_name])


# ── Swagger UI (unified) ──────────────────────────────────────────────────────

@app.get("/docs", include_in_schema=False)
async def swagger_ui() -> HTMLResponse:
    """Serve the unified Swagger UI."""
    service_options = "\n".join(
        f'            <option value="/services/{name}/openapi.json">{meta["label"]}</option>'
        for name, meta in SERVICES.items()
        if name in _spec_cache
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>DATA ENGINE — API Docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui.css" />
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #0f1117; color: #e2e8f0; }}

    /* ── Top bar ── */
    #topbar {{
      display: flex; align-items: center; gap: 16px;
      padding: 12px 24px;
      background: #1a1d2e;
      border-bottom: 1px solid #2d3149;
      position: sticky; top: 0; z-index: 100;
      box-shadow: 0 2px 12px rgba(0,0,0,.4);
    }}
    #topbar .logo {{
      font-weight: 700; font-size: 15px; letter-spacing: .05em;
      background: linear-gradient(90deg, #6366f1, #8b5cf6);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
      white-space: nowrap;
    }}
    #topbar label {{ font-size: 12px; color: #94a3b8; white-space: nowrap; }}
    #service-select {{
      flex: 1; max-width: 320px;
      background: #0f1117; color: #e2e8f0;
      border: 1px solid #2d3149; border-radius: 6px;
      padding: 6px 10px; font-size: 13px; cursor: pointer;
    }}
    #service-select:focus {{ outline: 2px solid #6366f1; }}
    .btn {{
      padding: 7px 16px; border-radius: 6px; border: none;
      font-size: 12px; font-weight: 600; cursor: pointer; transition: opacity .15s;
    }}
    .btn:hover {{ opacity: .85; }}
    .btn-primary {{ background: #6366f1; color: #fff; }}
    .btn-secondary {{ background: #1e2235; color: #94a3b8; border: 1px solid #2d3149; }}
    #status-dot {{
      width: 8px; height: 8px; border-radius: 50%;
      background: #22c55e; flex-shrink: 0;
      box-shadow: 0 0 6px #22c55e;
    }}
    #status-dot.loading {{ background: #f59e0b; box-shadow: 0 0 6px #f59e0b; animation: pulse 1s infinite; }}
    @keyframes pulse {{ 50% {{ opacity: .3; }} }}
    #spec-label {{ font-size: 11px; color: #64748b; }}

    /* ── Swagger UI overrides ── */
    .swagger-ui {{ background: transparent !important; }}
    .swagger-ui .topbar {{ display: none !important; }}
    .swagger-ui .info {{ padding: 20px 0 10px; }}
    .swagger-ui .info .title {{ color: #e2e8f0 !important; }}
    .swagger-ui .info p, .swagger-ui .info li {{ color: #94a3b8 !important; }}
    .swagger-ui .info a {{ color: #6366f1 !important; }}
    .swagger-ui .scheme-container {{ background: #1a1d2e !important; padding: 12px 20px; border-radius: 8px; margin-bottom: 16px; }}
    .swagger-ui section.models {{ background: #1a1d2e !important; border-radius: 8px; }}
    .swagger-ui .opblock-tag {{ color: #e2e8f0 !important; border-bottom-color: #2d3149 !important; }}
    .swagger-ui .opblock {{ border-radius: 8px !important; margin-bottom: 6px !important; }}
    .swagger-ui .opblock .opblock-summary {{ border-radius: 8px !important; }}
    .swagger-ui input, .swagger-ui textarea, .swagger-ui select {{
      background: #0f1117 !important; color: #e2e8f0 !important;
      border-color: #2d3149 !important; border-radius: 6px !important;
    }}
    .swagger-ui .btn {{ border-radius: 6px !important; }}
    #swagger-ui {{ padding: 0 24px 40px; }}
  </style>
</head>
<body>

<div id="topbar">
  <span class="logo">⚡ DATA ENGINE</span>
  <span id="status-dot" class="loading"></span>
  <label for="service-select">View:</label>
  <select id="service-select" onchange="loadSpec(this.value)">
    <option value="/openapi.json" selected>All Services (Unified)</option>
    {service_options}
  </select>
  <span id="spec-label">unified spec</span>
  <button class="btn btn-secondary" onclick="refreshSpecs()">↻ Refresh</button>
  <button class="btn btn-primary" onclick="downloadSpec()">⬇ Download YAML</button>
</div>

<div id="swagger-ui"></div>

<script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script src="https://unpkg.com/swagger-ui-dist@5.17.14/swagger-ui-standalone-preset.js"></script>
<script>
  let ui;
  let currentSpecUrl = "/openapi.json";

  function buildUI(specUrl) {{
    currentSpecUrl = specUrl;
    if (ui) {{ ui = null; document.getElementById("swagger-ui").innerHTML = ""; }}
    document.getElementById("status-dot").className = "loading";
    ui = SwaggerUIBundle({{
      url:           specUrl,
      dom_id:        "#swagger-ui",
      presets:       [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
      layout:        "StandaloneLayout",
      deepLinking:   true,
      displayRequestDuration: true,
      filter:        true,
      tryItOutEnabled: true,
      persistAuthorization: true,
      onComplete: () => {{ document.getElementById("status-dot").className = ""; }},
    }});
  }}

  function loadSpec(url) {{
    const sel    = document.getElementById("service-select");
    const label  = sel.options[sel.selectedIndex].text;
    document.getElementById("spec-label").textContent = label;
    buildUI(url);
  }}

  function refreshSpecs() {{
    fetch("/refresh", {{method: "POST"}})
      .then(r => r.json())
      .then(d => {{ alert(`Refreshed ${{d.refreshed}}/${{d.total}} services.`); buildUI(currentSpecUrl); }});
  }}

  function downloadSpec() {{
    window.open("/openapi.yaml", "_blank");
  }}

  buildUI("/openapi.json");
</script>
</body>
</html>"""
    return HTMLResponse(html)


# ── ReDoc ─────────────────────────────────────────────────────────────────────

@app.get("/redoc", include_in_schema=False)
async def redoc() -> HTMLResponse:
    """Serve the unified ReDoc documentation."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>DATA ENGINE — API Reference (ReDoc)</title>
  <style>body{margin:0;padding:0;}</style>
</head>
<body>
  <redoc spec-url="/openapi.json"
         expand-responses="200,201"
         hide-download-button="false"
         theme='{"colors":{"primary":{"main":"#6366f1"}},"typography":{"fontFamily":"system-ui,sans-serif"}}'
  ></redoc>
  <script src="https://cdn.jsdelivr.net/npm/redoc@latest/bundles/redoc.standalone.js"></script>
</body>
</html>"""
    return HTMLResponse(html)


# ── Root redirect ─────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/docs")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "docs.swagger_server:app",
        host="0.0.0.0",
        port=8099,
        reload=False,
        log_level="info",
    )
