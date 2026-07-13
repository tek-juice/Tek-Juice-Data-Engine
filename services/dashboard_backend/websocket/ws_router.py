"""
DATA ENGINE — Dashboard WebSocket Router
Real-time activity feed for the telemetry stream viewer.
Phase 3: Central Command — live heatmap and event streaming.
"""

import asyncio
import json
import structlog
from datetime import datetime, UTC

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import JWTError

from configs.security import decode_token

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["WebSocket"])

# Active WebSocket connections keyed by tenant_id
_connections: dict[str, list[WebSocket]] = {}


@router.websocket("/activity")
async def activity_feed(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token for authentication"),
):
    """
    WebSocket endpoint for live system activity feed.
    Clients connect and receive real-time JSON events as they are processed.

    Connect with:
        ws://localhost:8011/ws/activity?token=<jwt>
    """
    # Authenticate before accepting
    try:
        payload = decode_token(token)
        tenant_id = payload.get("tenant_id", "unknown")
    except JWTError:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    logger.info("ws_client_connected", tenant_id=tenant_id)

    if tenant_id not in _connections:
        _connections[tenant_id] = []
    _connections[tenant_id].append(websocket)

    try:
        # Send welcome message
        await websocket.send_json({
            "type":      "connected",
            "message":   "DATA ENGINE — Live Activity Feed",
            "tenant_id": tenant_id,
            "timestamp": datetime.now(UTC).isoformat(),
        })

        # Keep connection alive and send periodic heartbeats
        while True:
            try:
                # Check for messages from client (ping/pong / filter updates)
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": datetime.now(UTC).isoformat()})
            except asyncio.TimeoutError:
                # Send heartbeat every 30 seconds
                await websocket.send_json({"type": "heartbeat", "timestamp": datetime.now(UTC).isoformat()})

    except WebSocketDisconnect:
        logger.info("ws_client_disconnected", tenant_id=tenant_id)
    except Exception as exc:
        logger.error("ws_error", tenant_id=tenant_id, error=str(exc))
    finally:
        if tenant_id in _connections:
            _connections[tenant_id] = [
                c for c in _connections[tenant_id] if c != websocket
            ]


async def broadcast_event(tenant_id: str, event: dict) -> None:
    """
    Broadcast an event to all connected WebSocket clients for a tenant.
    Called by the telemetry monitor and event bus.
    """
    connections = _connections.get(tenant_id, []) + _connections.get("*", [])
    if not connections:
        return

    dead: list[WebSocket] = []
    for ws in connections:
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)

    # Clean up dead connections
    if dead and tenant_id in _connections:
        _connections[tenant_id] = [c for c in _connections[tenant_id] if c not in dead]


def connection_count() -> int:
    """Return total active WebSocket connections."""
    return sum(len(v) for v in _connections.values())
