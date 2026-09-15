"""
DATA ENGINE — Shopify Injector
Publishes AI-generated content into Shopify blogs and pages
via the Shopify Admin REST API.
"""

import structlog
import httpx
from typing import Any

logger = structlog.get_logger(__name__)


class ShopifyInjector:
    """
    Injects content into Shopify via the Admin API.

    Required config keys:
        shop_domain  : e.g. tekjuice.myshopify.com
        access_token : Admin API access token from private app
        blog_id      : Shopify blog ID to publish articles into
                       (fetched automatically if not provided)
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.shop_domain  = config["shop_domain"].rstrip("/")
        self.access_token = config["access_token"]
        self.blog_id      = config.get("blog_id")
        self._base        = f"https://{self.shop_domain}/admin/api/2024-01"
        self._headers     = {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }

    async def test_connection(self) -> dict[str, Any]:
        """Verify credentials have read/write access to blog content."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self._base}/blogs.json",
                    headers=self._headers,
                )
            if r.status_code == 200:
                blogs = r.json().get("blogs", [])
                if blogs and not self.blog_id:
                    self.blog_id = blogs[0]["id"]
                return {"success": True, "blog_count": len(blogs)}
            if r.status_code == 401:
                return {"success": False, "error": "Invalid access token. Please re-generate from Shopify Admin → Apps."}
            if r.status_code == 403:
                return {"success": False, "error": "Token does not have write_content permission. Please re-create the app with that scope enabled."}
            return {"success": False, "error": f"Shopify returned {r.status_code}."}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def _ensure_blog_id(self) -> int | None:
        """Auto-fetch the first blog ID if not set."""
        if self.blog_id:
            return self.blog_id
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{self._base}/blogs.json", headers=self._headers)
            blogs = r.json().get("blogs", [])
            if blogs:
                self.blog_id = blogs[0]["id"]
                return self.blog_id
        except Exception:
            pass
        return None

    async def publish_article(
        self,
        title:            str,
        body:             str,
        summary:          str = "",
        tags:             list[str] | None = None,
        published:        bool = True,
    ) -> dict[str, Any]:
        """
        Create or update a Shopify blog article.
        """
        blog_id = await self._ensure_blog_id()
        if not blog_id:
            return {"success": False, "error": "No blog found on this Shopify store. Please create a blog first via Shopify Admin → Online Store → Blog Posts."}

        article: dict[str, Any] = {
            "article": {
                "title":     title,
                "body_html": body,
                "summary":   summary,
                "published": published,
                "tags":      ", ".join(tags) if tags else "",
            }
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{self._base}/blogs/{blog_id}/articles.json",
                    json=article,
                    headers=self._headers,
                )
            if r.status_code == 201:
                data = r.json().get("article", {})
                logger.info("shopify_article_published", title=title, article_id=data.get("id"))
                return {"success": True, "article_id": data.get("id"), "url": data.get("url")}
            return {"success": False, "error": f"Shopify returned {r.status_code}: {r.text[:200]}"}
        except Exception as exc:
            logger.error("shopify_publish_failed", title=title, error=str(exc))
            return {"success": False, "error": str(exc)}
