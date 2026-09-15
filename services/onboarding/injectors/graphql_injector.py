"""
DATA ENGINE — GraphQL Injector
Introspects a product's GraphQL schema and injects content
via existing content mutations — no installation required.
"""

import structlog
import httpx
from typing import Any

logger = structlog.get_logger(__name__)

# Common mutation name patterns that accept content creation
_CONTENT_MUTATION_PATTERNS = [
    "createPost", "createArticle", "createBlogPost", "createPage",
    "createContent", "insertPost", "publishPost", "addPost",
    "createEntry", "upsertPost",
]


class GraphQLInjector:
    """
    Injects content into a backend that exposes a GraphQL API.

    Required config keys:
        endpoint      : Full GraphQL endpoint URL e.g. https://site.com/graphql
        auth_token    : Bearer token or API key for authentication
        mutation_name : Auto-detected from schema introspection
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.endpoint      = config["endpoint"]
        self.auth_token    = config["auth_token"]
        self.mutation_name = config.get("mutation_name")
        self._headers      = {
            "Authorization": f"Bearer {self.auth_token}",
            "Content-Type":  "application/json",
        }

    async def test_connection(self) -> dict[str, Any]:
        """
        Introspect the GraphQL schema to verify access and discover
        the correct content creation mutation.
        """
        introspection = """
        {
          __schema {
            mutationType {
              fields {
                name
                args { name type { name kind ofType { name } } }
              }
            }
          }
        }
        """
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r = await client.post(
                    self.endpoint,
                    json={"query": introspection},
                    headers=self._headers,
                )

            if r.status_code == 401:
                return {"success": False, "error": "Invalid auth token for this GraphQL API."}
            if r.status_code != 200:
                return {"success": False, "error": f"GraphQL endpoint returned {r.status_code}."}

            data = r.json()
            mutation_type = data.get("data", {}).get("__schema", {}).get("mutationType")
            if not mutation_type:
                return {"success": False, "error": "This GraphQL API does not expose mutations — content cannot be injected through it."}

            # Find a matching content mutation
            fields = mutation_type.get("fields", [])
            for pattern in _CONTENT_MUTATION_PATTERNS:
                for field in fields:
                    if field["name"].lower() == pattern.lower():
                        self.mutation_name = field["name"]
                        logger.info("graphql_mutation_discovered", mutation=self.mutation_name)
                        return {"success": True, "mutation": self.mutation_name}

            # No match — return list of available mutations so user can pick
            available = [f["name"] for f in fields[:10]]
            return {
                "success": False,
                "error": f"Could not auto-detect a content mutation. Available mutations: {', '.join(available)}. Please contact Tek Juice support with this list.",
            }

        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def publish(
        self,
        title: str,
        body:  str,
        slug:  str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the discovered mutation to create a content item."""
        if not self.mutation_name:
            result = await self.test_connection()
            if not result.get("success"):
                return result

        variables = {"title": title, "body": body, "slug": slug, **(extra or {})}
        query = f"""
        mutation PublishContent($title: String!, $body: String!, $slug: String!) {{
          {self.mutation_name}(input: {{ title: $title, body: $body, slug: $slug }}) {{
            id
            title
          }}
        }}
        """

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    self.endpoint,
                    json={"query": query, "variables": variables},
                    headers=self._headers,
                )
            if r.status_code == 200:
                data = r.json()
                if "errors" in data:
                    return {"success": False, "error": str(data["errors"])[:300]}
                logger.info("graphql_content_published", title=title, slug=slug)
                return {"success": True, "data": data.get("data")}
            return {"success": False, "error": f"GraphQL returned {r.status_code}: {r.text[:200]}"}
        except Exception as exc:
            logger.error("graphql_publish_failed", title=title, error=str(exc))
            return {"success": False, "error": str(exc)}
