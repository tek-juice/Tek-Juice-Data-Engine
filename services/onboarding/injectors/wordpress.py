"""
DATA ENGINE — WordPress Injector
Publishes AI-generated content directly into a WordPress site
via the WordPress REST API using Application Password authentication.
"""

import structlog
import httpx
from typing import Any

logger = structlog.get_logger(__name__)


class WordPressInjector:
    """
    Injects content into WordPress via the REST API.

    Required config keys (stored encrypted on the tenant):
        site_url    : e.g. https://tekjuice.co.ke
        username    : WordPress username (admin or editor)
        app_password: Application Password generated in WP Users → Profile
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.site_url    = config["site_url"].rstrip("/")
        self.username    = config["username"]
        self.app_password= config["app_password"]
        self._auth       = (self.username, self.app_password)
        self._api        = f"{self.site_url}/wp-json/wp/v2"

    async def test_connection(self) -> dict[str, Any]:
        """
        Verify credentials are valid and we have publish access.
        Returns {"success": True} or {"success": False, "error": "..."}
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self._api}/users/me",
                    auth=self._auth,
                )
            if r.status_code == 200:
                data = r.json()
                caps = data.get("capabilities", {})
                if not (caps.get("publish_posts") or caps.get("administrator") or caps.get("editor")):
                    return {"success": False, "error": "This WordPress user does not have publish permission. Please use an Admin or Editor account."}
                return {"success": True, "username": data.get("name")}
            if r.status_code == 401:
                return {"success": False, "error": "Invalid username or Application Password. Please check Step 5 of the guide."}
            return {"success": False, "error": f"WordPress returned status {r.status_code}."}
        except httpx.ConnectError:
            return {"success": False, "error": f"Could not reach {self.site_url}. Please check the URL."}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def publish_post(
        self,
        title:            str,
        body:             str,
        slug:             str,
        meta_description: str = "",
        keywords:         list[str] | None = None,
        schema_markup:    dict | None = None,
        category_ids:     list[int] | None = None,
        status:           str = "publish",
    ) -> dict[str, Any]:
        """
        Create or update a post in WordPress.
        Returns the post ID and URL on success.
        """
        payload: dict[str, Any] = {
            "title":   title,
            "content": body,
            "slug":    slug,
            "status":  status,
        }
        if category_ids:
            payload["categories"] = category_ids

        # Inject meta description + schema via Yoast SEO or RankMath meta fields
        if meta_description:
            payload.setdefault("meta", {})["_yoast_wpseo_metadesc"] = meta_description

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                # Check if post with this slug already exists
                existing = await client.get(
                    f"{self._api}/posts",
                    params={"slug": slug},
                    auth=self._auth,
                )
                posts = existing.json() if existing.status_code == 200 else []

                if posts:
                    # Update existing post
                    post_id = posts[0]["id"]
                    r = await client.post(
                        f"{self._api}/posts/{post_id}",
                        json=payload,
                        auth=self._auth,
                    )
                else:
                    # Create new post
                    r = await client.post(
                        f"{self._api}/posts",
                        json=payload,
                        auth=self._auth,
                    )

            if r.status_code in (200, 201):
                data = r.json()
                logger.info("wordpress_post_published", slug=slug, post_id=data.get("id"))
                return {"success": True, "post_id": data.get("id"), "url": data.get("link")}

            return {"success": False, "error": f"WordPress returned {r.status_code}: {r.text[:200]}"}

        except Exception as exc:
            logger.error("wordpress_publish_failed", slug=slug, error=str(exc))
            return {"success": False, "error": str(exc)}
