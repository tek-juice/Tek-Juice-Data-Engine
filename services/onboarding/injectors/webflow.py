"""
DATA ENGINE — Webflow Injector
Publishes AI-generated content into Webflow CMS Collections via the Webflow API.
"""

import structlog
import httpx
from typing import Any

logger = structlog.get_logger(__name__)

_WEBFLOW_API = "https://api.webflow.com/v2"


class WebflowInjector:
    """
    Injects content into Webflow via the CMS Collections API.

    Required config keys:
        api_token     : Webflow API token from Project Settings → Integrations
        collection_id : The CMS Collection ID to publish items into
                        (auto-detected if not provided)
        site_id       : Webflow site ID (auto-detected)
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.api_token     = config["api_token"]
        self.collection_id = config.get("collection_id")
        self.site_id       = config.get("site_id")
        self._headers      = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type":  "application/json",
            "accept":        "application/json",
        }

    async def test_connection(self) -> dict[str, Any]:
        """Verify API token and discover site/collection if not provided."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{_WEBFLOW_API}/token/authorized_by", headers=self._headers)
            if r.status_code == 200:
                # Auto-discover site ID
                if not self.site_id:
                    await self._discover_site()
                return {"success": True}
            if r.status_code == 401:
                return {"success": False, "error": "Invalid API token. Please re-generate from Webflow Project Settings → Integrations."}
            return {"success": False, "error": f"Webflow returned {r.status_code}."}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def _discover_site(self) -> None:
        """Auto-fetch site ID and first CMS collection ID."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{_WEBFLOW_API}/sites", headers=self._headers)
                sites = r.json().get("sites", [])
                if sites:
                    self.site_id = sites[0]["id"]
                    # Fetch collections for this site
                    rc = await client.get(
                        f"{_WEBFLOW_API}/sites/{self.site_id}/collections",
                        headers=self._headers,
                    )
                    collections = rc.json().get("collections", [])
                    if collections and not self.collection_id:
                        # Prefer a collection with "blog" or "post" in its name
                        for col in collections:
                            if any(kw in col.get("displayName", "").lower() for kw in ("blog", "post", "article", "news")):
                                self.collection_id = col["id"]
                                break
                        if not self.collection_id:
                            self.collection_id = collections[0]["id"]
        except Exception as exc:
            logger.warning("webflow_discover_failed", error=str(exc))

    async def publish_item(
        self,
        title:   str,
        body:    str,
        slug:    str,
        excerpt: str = "",
        publish: bool = True,
    ) -> dict[str, Any]:
        """Create a new CMS item in the Webflow collection and publish it."""
        if not self.collection_id:
            await self._discover_site()
        if not self.collection_id:
            return {"success": False, "error": "Could not find a CMS collection in your Webflow site. Please provide the Collection ID manually."}

        payload = {
            "fieldData": {
                "name":    title,
                "slug":    slug,
                "post-body":  body,
                "post-summary": excerpt,
            },
            "isDraft":     False,
            "isArchived":  False,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{_WEBFLOW_API}/collections/{self.collection_id}/items",
                    json=payload,
                    headers=self._headers,
                )
                if r.status_code in (200, 201, 202):
                    data = r.json()
                    item_id = data.get("id")
                    if publish and item_id and self.site_id:
                        await client.post(
                            f"{_WEBFLOW_API}/sites/{self.site_id}/publish",
                            json={"domains": []},
                            headers=self._headers,
                        )
                    logger.info("webflow_item_published", title=title, item_id=item_id)
                    return {"success": True, "item_id": item_id}

            return {"success": False, "error": f"Webflow returned {r.status_code}: {r.text[:200]}"}
        except Exception as exc:
            logger.error("webflow_publish_failed", title=title, error=str(exc))
            return {"success": False, "error": str(exc)}
