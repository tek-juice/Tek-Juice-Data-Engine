"""
DATA ENGINE — LEO (LLM Engine Optimisation) Public API
Pillar 1: Provides structured inventory, pricing, and availability data
directly to AI engines via public endpoints + Schema.org markup.

Strategy:
  - AI systems (Perplexity, Bing Copilot, ChatGPT Browse) query public APIs
    rather than parsing HTML when a machine-readable feed is available.
  - Exposing /api/v1/leo/inventory, /pricing, and /availability as clean JSON
    endpoints that also return Schema.org JSON-LD allows AI engines to ingest
    live product/service data without depending on page layout.
  - Each response includes an embedded `schema_org` key with a ready-to-embed
    JSON-LD block (Product, Offer, ItemList) so crawlers get structured data
    directly from the API response — no HTML parsing required.

Schema.org types used:
  Product, Offer, ItemList, ListItem, ItemAvailability
"""

from __future__ import annotations

from datetime import datetime, UTC
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

SCHEMA_ORG = "https://schema.org"


# ── Availability mapping ───────────────────────────────────────────────────────

AVAILABILITY_MAP: dict[str, str] = {
    "in_stock":         "https://schema.org/InStock",
    "out_of_stock":     "https://schema.org/OutOfStock",
    "pre_order":        "https://schema.org/PreOrder",
    "discontinued":     "https://schema.org/Discontinued",
    "limited":          "https://schema.org/LimitedAvailability",
    "back_order":       "https://schema.org/BackOrder",
    "online_only":      "https://schema.org/OnlineOnly",
    "in_store_only":    "https://schema.org/InStoreOnly",
}


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class LEOOffer:
    """A single pricing offer for an item."""
    price: float
    currency: str = "USD"
    availability: str = "in_stock"       # key from AVAILABILITY_MAP
    price_valid_until: str = ""          # ISO date e.g. "2025-12-31"
    url: str = ""
    seller_name: str = ""

    def to_schema_org(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "@type":        "Offer",
            "price":        self.price,
            "priceCurrency": self.currency,
            "availability": AVAILABILITY_MAP.get(self.availability, AVAILABILITY_MAP["in_stock"]),
        }
        if self.price_valid_until:
            schema["priceValidUntil"] = self.price_valid_until
        if self.url:
            schema["url"] = self.url
        if self.seller_name:
            schema["seller"] = {"@type": "Organization", "name": self.seller_name}
        return schema


@dataclass
class LEOItem:
    """
    An inventory item exposed via the LEO public API.
    Maps directly to Schema.org Product with nested Offer.
    """
    id: str
    name: str
    description: str
    brand: str = ""
    sku: str = ""
    gtin: str = ""          # GTIN-13 / EAN
    category: str = ""
    url: str = ""
    image_url: str = ""
    offers: list[LEOOffer] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    # AI-specific fields
    ai_summary: str = ""    # ≤ 40-word machine-scannable summary for AI citation
    last_updated: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat()
    )

    def to_schema_org(self) -> dict[str, Any]:
        """
        Build a Schema.org Product JSON-LD block.
        The ai_summary maps to the description — it must follow the
        First Sentence Rule (direct, factual, entity-first).
        """
        schema: dict[str, Any] = {
            "@context": SCHEMA_ORG,
            "@type":    "Product",
            "name":     self.name,
            "description": self.ai_summary or self.description,
            "dateModified": self.last_updated[:10],
        }
        if self.brand:
            schema["brand"] = {"@type": "Brand", "name": self.brand}
        if self.sku:
            schema["sku"] = self.sku
        if self.gtin:
            schema["gtin13"] = self.gtin
        if self.category:
            schema["category"] = self.category
        if self.url:
            schema["url"] = self.url
        if self.image_url:
            schema["image"] = {"@type": "ImageObject", "url": self.image_url}
        if self.tags:
            schema["keywords"] = ", ".join(self.tags)
        if self.offers:
            offer_schemas = [o.to_schema_org() for o in self.offers]
            schema["offers"] = offer_schemas[0] if len(offer_schemas) == 1 else offer_schemas
        return schema

    def to_leo_dict(self) -> dict[str, Any]:
        """
        Full LEO API response payload for a single item.
        Includes raw fields AND embedded Schema.org markup so AI engines
        can consume structured data directly without HTML parsing.
        """
        return {
            "id":           self.id,
            "name":         self.name,
            "description":  self.description,
            "ai_summary":   self.ai_summary or self.description[:200],
            "brand":        self.brand,
            "sku":          self.sku,
            "gtin":         self.gtin,
            "category":     self.category,
            "url":          self.url,
            "image_url":    self.image_url,
            "tags":         self.tags,
            "last_updated": self.last_updated,
            "offers": [
                {
                    "price":        o.price,
                    "currency":     o.currency,
                    "availability": o.availability,
                    "url":          o.url,
                }
                for o in self.offers
            ],
            # Embedded Schema.org — machine-readable by AI crawlers
            "schema_org": self.to_schema_org(),
        }


# ── LEO Inventory Feed ─────────────────────────────────────────────────────────

@dataclass
class LEOInventoryFeed:
    """
    A paginated feed of inventory items in both API and Schema.org format.
    The schema_org key contains an ItemList suitable for embedding in <head>
    or returning directly from a /.well-known/inventory.json endpoint.
    """
    items: list[LEOItem]
    total: int
    page: int
    page_size: int
    tenant_name: str = ""
    feed_url: str = ""

    def to_response(self) -> dict[str, Any]:
        item_dicts = [i.to_leo_dict() for i in self.items]

        # Build Schema.org ItemList for the full page
        item_list_schema: dict[str, Any] = {
            "@context": SCHEMA_ORG,
            "@type":    "ItemList",
            "name":     f"{self.tenant_name} Inventory" if self.tenant_name else "Inventory",
            "numberOfItems": self.total,
            "itemListElement": [
                {
                    "@type":    "ListItem",
                    "position": (self.page - 1) * self.page_size + idx + 1,
                    "item":     item["schema_org"],
                }
                for idx, item in enumerate(item_dicts)
            ],
        }
        if self.feed_url:
            item_list_schema["url"] = self.feed_url

        return {
            "page":       self.page,
            "page_size":  self.page_size,
            "total":      self.total,
            "items":      item_dicts,
            # Top-level Schema.org block — embed in <head> or return from API
            "schema_org": item_list_schema,
        }


# ── LEO Builder ───────────────────────────────────────────────────────────────

class LEOBuilder:
    """
    Builds LEO-compliant API responses from raw inventory data.

    Enforces AI-readiness rules:
    1. Every item MUST have an ai_summary ≤ 40 words following the
       First Sentence Rule (entity-first, direct, factual).
    2. Every offer MUST include availability mapped to Schema.org URI.
    3. The response embeds machine-readable Schema.org JSON-LD so AI
       engines get structured data without HTML layout dependency.
    """

    def build_item(
        self,
        id: str,
        name: str,
        description: str,
        offers: list[dict],
        brand: str = "",
        sku: str = "",
        gtin: str = "",
        category: str = "",
        url: str = "",
        image_url: str = "",
        tags: list[str] | None = None,
        ai_summary: str = "",
    ) -> LEOItem:
        """
        Build a single LEOItem, auto-generating ai_summary if not provided.

        The ai_summary is the most critical LEO field: it is the text an AI
        engine will use when citing this product in a response. It must:
          ✓ Start with the product name (entity-first)
          ✓ State what the product IS or DOES immediately
          ✓ Include one concrete differentiator (stat, feature, or price)
          ✓ Be ≤ 40 words
        """
        parsed_offers = [
            LEOOffer(
                price=float(o.get("price", 0)),
                currency=o.get("currency", "USD"),
                availability=o.get("availability", "in_stock"),
                price_valid_until=o.get("price_valid_until", ""),
                url=o.get("url", url),
                seller_name=o.get("seller_name", brand),
            )
            for o in offers
        ]

        if not ai_summary:
            ai_summary = self._auto_summary(name, description, brand, parsed_offers)

        item = LEOItem(
            id=id,
            name=name,
            description=description,
            brand=brand,
            sku=sku,
            gtin=gtin,
            category=category,
            url=url,
            image_url=image_url,
            offers=parsed_offers,
            tags=tags or [],
            ai_summary=ai_summary,
        )

        logger.debug(
            "leo_item_built",
            item_id=id,
            name=name,
            offers=len(parsed_offers),
            summary_words=len(ai_summary.split()),
        )
        return item

    def build_feed(
        self,
        items: list[LEOItem],
        total: int,
        page: int = 1,
        page_size: int = 20,
        tenant_name: str = "",
        feed_url: str = "",
    ) -> LEOInventoryFeed:
        return LEOInventoryFeed(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            tenant_name=tenant_name,
            feed_url=feed_url,
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _auto_summary(
        name: str,
        description: str,
        brand: str,
        offers: list[LEOOffer],
    ) -> str:
        """
        Auto-generate a ≤ 40-word AI-ready summary.
        Pattern: "{Name} by {Brand} is {description[:120]}. {price_hint}."
        """
        base = description.split(".")[0].strip()
        if len(base.split()) > 25:
            base = " ".join(base.split()[:25])

        price_hint = ""
        if offers:
            o = offers[0]
            avail = o.availability.replace("_", " ")
            price_hint = f"Priced at {o.currency} {o.price:.2f}, {avail}."

        brand_prefix = f"{name} by {brand}" if brand else name
        summary = f"{brand_prefix} — {base}."
        if price_hint:
            summary = f"{summary} {price_hint}"

        words = summary.split()
        if len(words) > 40:
            summary = " ".join(words[:40]) + "."

        return summary
