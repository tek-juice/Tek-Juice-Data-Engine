"""
DATA ENGINE — Wix Injector
Publishes AI-generated content into Wix via the Wix Content Manager API.
"""

import structlog
import httpx
from typing import Any

logger = structlog.get_logger(__name__)

_WIX_API_BASE = "https://www.wixapis.com"


class WixInjector:
    """
    Injects content into Wix via the Wix Blog and Pages APIs.

    Required config keys:
        site_id   : Wix site ID (visible in the Wix dashboard URL)
        api_key   : API key from Wix Dashboard → Settings → API Keys
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.site_id = config["site_id"]
        self.api_key = config["api_key"]
        self._headers = {
            "Authorization": self.api_key,
            "wix-site-id":   self.site_id,
            "Content-Type":  "application/json",
        }

    async def test_connection(self) -> dict[str, Any]:
        """Verify API key and site ID are valid."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{_WIX_API_BASE}/site-properties/v4/properties",
                    headers=self._headers,
                )
            if r.status_code == 200:
                return {"success": True}
            if r.status_code == 401:
                return {"success": False, "error": "Invalid API key. Please re-generate from Wix Dashboard → Settings → API Keys."}
            if r.status_code == 403:
                return {"success": False, "error": "API key does not have required permissions. Please enable All Permissions when generating."}
            return {"success": False, "error": f"Wix returned {r.status_code}."}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def publish_post(
        self,
        title:   str,
        body:    str,
        excerpt: str = "",
        tags:    list[str] | None = None,
        publish: bool = True,
    ) -> dict[str, Any]:
        """Create a new blog post on the Wix site."""
        payload = {
            "post": {
                "title":           title,
                "richContent":     {"nodes": [{"type": "PARAGRAPH", "nodes": [{"type": "TEXT", "textData": {"text": body}}]}]},
                "excerpt":         excerpt,
                "tagIds":          [],
                "paidContent":     False,
            }
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{_WIX_API_BASE}/blog/v3/posts",
                    json=payload,
                    headers=self._headers,
                )
                if r.status_code in (200, 201):
                    data = r.json().get("post", {})
                    post_id = data.get("id")

                    if publish and post_id:
                        await client.post(
                            f"{_WIX_API_BASE}/blog/v3/posts/{post_id}/publish",
                            headers=self._headers,
                        )

                    logger.info("wix_post_published", title=title, post_id=post_id)
                    return {"success": True, "post_id": post_id}

            return {"success": False, "error": f"Wix returned {r.status_code}: {r.text[:200]}"}
        except Exception as exc:
            logger.error("wix_publish_failed", title=title, error=str(exc))
            return {"success": False, "error": str(exc)}
