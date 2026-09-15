"""
DATA ENGINE — SDK Router
Serves the JavaScript bridge script at GET /sdk.js?key=de_xxx
Receives page-live signals from connected product websites at POST /sdk/signal.

The script is embedded on the connected product's website via a <script> tag.
It signals the Data Engine when published content is live, enabling
the engine to confirm publication and begin rank monitoring.
"""

import hashlib

import structlog
from fastapi import APIRouter, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text

from configs.database import get_db_session
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from configs.settings import get_settings

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["SDK"])


@router.get("/sdk.js", include_in_schema=False)
async def serve_sdk(
    key: str = Query(..., description="Data Engine API key"),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Serve the JavaScript bridge script for a validated API key.
    Returns the script with the key embedded.
    Adds long cache headers so repeat page loads are instant.
    Returns 404 for invalid/revoked keys — prevents abuse.
    """
    s = get_settings()
    key_hash = hashlib.sha256(key.encode()).hexdigest()

    result = await db.execute(
        text("""
            SELECT ak.tenant_id, ak.is_active, t.name, t.is_active AS tenant_active,
                   t.injection_status
            FROM api_keys ak
            JOIN tenants t ON t.id = ak.tenant_id
            WHERE ak.key_hash = :key_hash
        """),
        {"key_hash": key_hash},
    )
    row = result.fetchone()

    if not row or not row.is_active or not row.tenant_active:
        return Response(
            content="/* Data Engine SDK — invalid or revoked key */",
            media_type="application/javascript",
            status_code=404,
        )

    tenant_id    = str(row.tenant_id)
    product_name = row.name
    engine_url   = s.engine_public_url

    js = _build_sdk(
        api_key      = key,
        tenant_id    = tenant_id,
        product_name = product_name,
        engine_url   = engine_url,
    )

    logger.debug("sdk_served", tenant_id=tenant_id)

    return Response(
        content=js,
        media_type="application/javascript",
        headers={
            "Cache-Control": "public, max-age=3600",
            "X-Engine":      "tek-juice-data-engine",
        },
    )


# ── POST /sdk/signal ──────────────────────────────────────────────────────────

class SignalPayload(BaseModel):
    event:     str
    tenant_id: str
    url:       str  = ""
    path:      str  = ""
    title:     str  = ""
    referrer:  str  = ""
    depth:     int  = 0
    ts:        int  = 0
    source:    str  = "sdk"


@router.post("/sdk/signal", status_code=204, include_in_schema=False)
async def receive_signal(
    payload: SignalPayload,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Receives page-live and engagement signals from the JavaScript SDK.
    Silently accepts and logs — returns 204 No Content for sendBeacon compatibility.
    """
    logger.debug(
        "sdk_signal",
        event     = payload.event,
        tenant_id = payload.tenant_id,
        path      = payload.path,
        title     = payload.title[:80] if payload.title else "",
    )
    # Future: persist to a telemetry table, trigger rank-check tasks, etc.
    return Response(status_code=204)



def _build_sdk(api_key: str, tenant_id: str, product_name: str, engine_url: str) -> str:
    """
    Build the JavaScript SDK string.
    The script does three things:
      1. Signals the engine that this page is live (confirms publication)
      2. Sends page metadata so the engine knows what is published where
      3. Exposes a DataEngine global for advanced integrations
    """
    return f"""/**
 * Tek Juice Data Engine SDK
 * Product: {product_name}
 * This script signals the Data Engine that content is live on this page.
 * DO NOT REMOVE — it enables automatic rank monitoring and content updates.
 */
(function() {{
  'use strict';

  var ENGINE_URL  = '{engine_url}';
  var API_KEY     = '{api_key}';
  var TENANT_ID   = '{tenant_id}';

  // ── Signal: page is live ────────────────────────────────────────────────
  function signalPageLive() {{
    try {{
      var payload = {{
        event:       'page.live',
        tenant_id:   TENANT_ID,
        url:         window.location.href,
        path:        window.location.pathname,
        title:       document.title,
        referrer:    document.referrer,
        source:      'sdk',
        ts:          Date.now(),
      }};

      // Use sendBeacon for reliability (survives page unload)
      if (navigator.sendBeacon) {{
        var blob = new Blob([JSON.stringify(payload)], {{type: 'application/json'}});
        navigator.sendBeacon(ENGINE_URL + '/sdk/signal', blob);
      }} else {{
        // Fallback: fire-and-forget fetch
        fetch(ENGINE_URL + '/sdk/signal', {{
          method:  'POST',
          headers: {{'Content-Type': 'application/json', 'X-API-Key': API_KEY}},
          body:    JSON.stringify(payload),
          keepalive: true,
        }}).catch(function() {{}});
      }}
    }} catch(e) {{}}
  }}

  // ── Signal: scroll depth (content engagement) ───────────────────────────
  var maxScroll = 0;
  function trackScroll() {{
    try {{
      var scrollPct = Math.round(
        (window.scrollY / (document.body.scrollHeight - window.innerHeight)) * 100
      );
      if (scrollPct > maxScroll + 25) {{
        maxScroll = scrollPct;
        navigator.sendBeacon && navigator.sendBeacon(ENGINE_URL + '/sdk/signal', new Blob(
          [JSON.stringify({{event:'scroll.depth', tenant_id:TENANT_ID,
            url:window.location.href, depth:scrollPct, ts:Date.now()}})],
          {{type:'application/json'}}
        ));
      }}
    }} catch(e) {{}}
  }}

  // ── Public API ───────────────────────────────────────────────────────────
  window.DataEngine = {{
    tenantId:    TENANT_ID,
    version:     '1.0.0',
    signal: function(eventName, data) {{
      try {{
        var payload = Object.assign({{event: eventName, tenant_id: TENANT_ID, ts: Date.now()}}, data || {{}});
        navigator.sendBeacon && navigator.sendBeacon(ENGINE_URL + '/sdk/signal',
          new Blob([JSON.stringify(payload)], {{type: 'application/json'}}));
      }} catch(e) {{}}
    }},
  }};

  // ── Init ─────────────────────────────────────────────────────────────────
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', signalPageLive);
  }} else {{
    signalPageLive();
  }}

  window.addEventListener('scroll', trackScroll, {{passive: true}});

}})();
"""
