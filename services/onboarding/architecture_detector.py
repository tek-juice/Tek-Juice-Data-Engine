"""
DATA ENGINE — Architecture Detector
Scans a product's website and identifies the exact platform and framework
from the technical fingerprints every system leaves behind.

Returns a DetectionResult containing:
  - platform_type  : canonical string identifier
  - display_name   : human-readable name for the wizard UI
  - confidence     : 0.0–1.0 how certain the detection is
  - signals        : list of signals that led to the conclusion
  - inject_strategy: which injector to use
  - credential_hint: plain-English description of what credential is needed
  - credential_guide: step-by-step instructions for the wizard UI
"""

from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ── Detection result ──────────────────────────────────────────────────────────

@dataclass
class DetectionResult:
    platform_type:    str
    display_name:     str
    confidence:       float
    signals:          list[str]
    inject_strategy:  str          # wordpress | shopify | wix | webflow | nextjs |
                                   # laravel | django | express | graphql |
                                   # headless_cms | ssh_custom | unknown
    credential_hint:  str          # shown in the wizard above the input field
    credential_guide: list[dict]   # [{step, instruction}] shown in the wizard


# ── Platform detection rules ──────────────────────────────────────────────────

async def detect(url: str) -> DetectionResult:
    """
    Fetch the website and run all detection rules.
    Returns the highest-confidence DetectionResult.
    Gracefully degrades to 'unknown' if the site is unreachable.
    """
    url = url.rstrip("/")
    html    = ""
    headers: dict[str, str] = {}

    # ── Fetch homepage ────────────────────────────────────────────────────────
    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "TekJuiceDataEngine/1.0 (+https://tekjuice.co.ke)"},
        ) as client:
            resp    = await client.get(url)
            html    = resp.text.lower()
            headers = {k.lower(): v.lower() for k, v in resp.headers.items()}
    except Exception as exc:
        logger.warning("architecture_detector_fetch_failed", url=url, error=str(exc))
        return _unknown(["Could not reach the website"])

    signals: list[str] = []

    # ── WordPress ─────────────────────────────────────────────────────────────
    wp_score = 0
    if "/wp-content/" in html:
        wp_score += 3
        signals.append("wp-content path in HTML")
    if "/wp-includes/" in html:
        wp_score += 2
        signals.append("wp-includes path in HTML")
    if 'name="generator" content="wordpress' in html:
        wp_score += 3
        signals.append("WordPress generator meta tag")
    if "wp-block-" in html:
        wp_score += 1
        signals.append("Gutenberg block classes")
    if "wordpress_logged_in" in html:
        wp_score += 1
        signals.append("WordPress login cookie pattern")
    if headers.get("x-powered-by", "").startswith("wp"):
        wp_score += 2
        signals.append("x-powered-by: WordPress header")
    # Check WP REST API availability
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
            r = await client.get(f"{url}/wp-json/wp/v2/")
            if r.status_code == 200 and "namespace" in r.text:
                wp_score += 4
                signals.append("WordPress REST API live at /wp-json/")
    except Exception:
        pass

    if wp_score >= 3:
        return DetectionResult(
            platform_type   = "wordpress",
            display_name    = "WordPress",
            confidence      = min(1.0, wp_score / 10),
            signals         = signals,
            inject_strategy = "wordpress",
            credential_hint = "WordPress Application Password",
            credential_guide= [
                {"step": "1", "instruction": "Log into your WordPress admin panel"},
                {"step": "2", "instruction": "Go to Users → Your Profile"},
                {"step": "3", "instruction": "Scroll down to the 'Application Passwords' section"},
                {"step": "4", "instruction": "Type any name (e.g. 'Data Engine') and click 'Add New Application Password'"},
                {"step": "5", "instruction": "WordPress shows you a password — copy it and paste it below"},
            ],
        )

    # ── Shopify ───────────────────────────────────────────────────────────────
    shopify_score = 0
    if "cdn.shopify.com" in html or "shopify.com/s/" in html:
        shopify_score += 3
        signals.append("Shopify CDN in HTML")
    if "shopify.shop" in html or "window.shopify" in html:
        shopify_score += 3
        signals.append("window.Shopify global variable")
    if ".myshopify.com" in html:
        shopify_score += 2
        signals.append(".myshopify.com reference in HTML")
    if "shopify-checkout-api-token" in html:
        shopify_score += 2
        signals.append("Shopify checkout API token meta tag")
    if "x-shopid" in headers or "x-shardid" in headers:
        shopify_score += 3
        signals.append("Shopify response headers detected")

    if shopify_score >= 3:
        return DetectionResult(
            platform_type   = "shopify",
            display_name    = "Shopify",
            confidence      = min(1.0, shopify_score / 10),
            signals         = signals,
            inject_strategy = "shopify",
            credential_hint = "Shopify Admin API Key",
            credential_guide= [
                {"step": "1", "instruction": "Log into your Shopify admin panel"},
                {"step": "2", "instruction": "Go to Settings → Apps and sales channels"},
                {"step": "3", "instruction": "Click 'Develop apps' then 'Create an app'"},
                {"step": "4", "instruction": "Name it 'Data Engine', click 'Configure Admin API scopes'"},
                {"step": "5", "instruction": "Enable: write_content, write_pages, write_blogs — then Save"},
                {"step": "6", "instruction": "Click 'Install app', then copy the Admin API access token and paste it below"},
            ],
        )

    # ── Wix ───────────────────────────────────────────────────────────────────
    wix_score = 0
    if "static.wixstatic.com" in html:
        wix_score += 3
        signals.append("Wix static CDN in HTML")
    if "wix.com" in html and ("editor" in html or "site" in html):
        wix_score += 2
        signals.append("Wix site reference in HTML")
    if "x-wix-" in str(headers):
        wix_score += 3
        signals.append("Wix response headers detected")
    if "_wixu" in html or "wixsite.com" in html:
        wix_score += 2
        signals.append("Wix user or domain pattern")

    if wix_score >= 3:
        return DetectionResult(
            platform_type   = "wix",
            display_name    = "Wix",
            confidence      = min(1.0, wix_score / 8),
            signals         = signals,
            inject_strategy = "wix",
            credential_hint = "Wix API Key",
            credential_guide= [
                {"step": "1", "instruction": "Log into your Wix account"},
                {"step": "2", "instruction": "Go to your site's Dashboard → Settings → Advanced → API Keys"},
                {"step": "3", "instruction": "Click 'Generate API Key'"},
                {"step": "4", "instruction": "Give it any name and enable 'All Permissions'"},
                {"step": "5", "instruction": "Copy the key and paste it below. Also copy your Site ID from the URL bar."},
            ],
        )

    # ── Webflow ───────────────────────────────────────────────────────────────
    webflow_score = 0
    if "assets.website-files.com" in html or "webflow.com" in html:
        webflow_score += 3
        signals.append("Webflow CDN in HTML")
    if 'data-wf-page' in html:
        webflow_score += 3
        signals.append("data-wf-page attribute in HTML")
    if 'data-wf-site' in html:
        webflow_score += 2
        signals.append("data-wf-site attribute in HTML")
    if "generator" in html and "webflow" in html:
        webflow_score += 2
        signals.append("Webflow generator meta tag")

    if webflow_score >= 3:
        return DetectionResult(
            platform_type   = "webflow",
            display_name    = "Webflow",
            confidence      = min(1.0, webflow_score / 8),
            signals         = signals,
            inject_strategy = "webflow",
            credential_hint = "Webflow API Token",
            credential_guide= [
                {"step": "1", "instruction": "Log into your Webflow account"},
                {"step": "2", "instruction": "Open your project → Project Settings → Integrations"},
                {"step": "3", "instruction": "Scroll to 'API Access' and click 'Generate API Token'"},
                {"step": "4", "instruction": "Copy the token and paste it below. Also note your Collection ID from the CMS panel."},
            ],
        )

    # ── Next.js ───────────────────────────────────────────────────────────────
    nextjs_score = 0
    if "__next_data__" in html or "/_next/static/" in html:
        nextjs_score += 4
        signals.append("Next.js __NEXT_DATA__ or /_next/static/ in HTML")
    if "next.js" in headers.get("x-powered-by", ""):
        nextjs_score += 3
        signals.append("x-powered-by: Next.js header")
    if "vercel" in headers.get("server", "") or "vercel" in str(headers):
        nextjs_score += 2
        signals.append("Hosted on Vercel — likely Next.js")
    if "__nextjs_" in html:
        nextjs_score += 2
        signals.append("Next.js internal script markers")

    if nextjs_score >= 3:
        return DetectionResult(
            platform_type   = "nextjs",
            display_name    = "Next.js",
            confidence      = min(1.0, nextjs_score / 8),
            signals         = signals,
            inject_strategy = "ssh_custom",
            credential_hint = "Server SSH Credentials",
            credential_guide= [
                {"step": "1", "instruction": "You need SSH access to the server where your Next.js app is deployed"},
                {"step": "2", "instruction": "Enter your server's hostname or IP address below"},
                {"step": "3", "instruction": "Enter your SSH username (usually 'ubuntu', 'root', or your custom username)"},
                {"step": "4", "instruction": "Enter your SSH password or paste your private key"},
                {"step": "5", "instruction": "The engine will connect, install the receiver automatically, and disconnect. No other changes will be made."},
            ],
        )

    # ── GraphQL API ───────────────────────────────────────────────────────────
    graphql_live = False
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            for gql_path in [f"{url}/graphql", f"{url}/api/graphql"]:
                r = await client.post(
                    gql_path,
                    json={"query": "{__typename}"},
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code in (200, 400) and "__typename" in r.text or "data" in r.text:
                    graphql_live = True
                    signals.append(f"GraphQL endpoint live at {gql_path}")
                    break
    except Exception:
        pass

    if graphql_live:
        return DetectionResult(
            platform_type   = "graphql",
            display_name    = "GraphQL API",
            confidence      = 0.9,
            signals         = signals,
            inject_strategy = "graphql",
            credential_hint = "GraphQL API Authentication Token",
            credential_guide= [
                {"step": "1", "instruction": "Find your GraphQL API's authentication token (check your backend's admin panel or .env file)"},
                {"step": "2", "instruction": "Paste the token below — the engine will introspect your schema and inject content using your existing mutations"},
            ],
        )

    # ── Laravel / PHP ─────────────────────────────────────────────────────────
    laravel_score = 0
    if "laravel_session" in html or "laravel" in headers.get("set-cookie", ""):
        laravel_score += 4
        signals.append("laravel_session cookie pattern")
    if 'name="csrf-token"' in html and "php" in headers.get("x-powered-by", ""):
        laravel_score += 3
        signals.append("CSRF token meta + PHP header")
    if "/storage/app/" in html or "/public/storage/" in html:
        laravel_score += 2
        signals.append("Laravel storage path in HTML")
    if "php" in headers.get("x-powered-by", ""):
        laravel_score += 1
        signals.append("x-powered-by: PHP header")

    if laravel_score >= 3:
        return DetectionResult(
            platform_type   = "laravel",
            display_name    = "Laravel (PHP)",
            confidence      = min(1.0, laravel_score / 8),
            signals         = signals,
            inject_strategy = "ssh_custom",
            credential_hint = "Server SSH Credentials",
            credential_guide= [
                {"step": "1", "instruction": "You need SSH access to the server where your Laravel app is running"},
                {"step": "2", "instruction": "Enter your server's hostname or IP address below"},
                {"step": "3", "instruction": "Enter your SSH username"},
                {"step": "4", "instruction": "Enter your SSH password or paste your private key"},
                {"step": "5", "instruction": "The engine will install a Laravel receiver package automatically and disconnect. Nothing else changes."},
            ],
        )

    # ── Django / Python ───────────────────────────────────────────────────────
    django_score = 0
    if "csrftoken" in html or "csrfmiddlewaretoken" in html:
        django_score += 3
        signals.append("Django CSRF token pattern")
    if "django" in headers.get("x-powered-by", "") or "python" in headers.get("server", ""):
        django_score += 3
        signals.append("Python/Django server header")
    if "/static/" in html and "django" in html:
        django_score += 1
        signals.append("Django static file path pattern")
    if "x-frame-options" in headers and "sameorigin" in headers.get("x-frame-options", ""):
        django_score += 1
        signals.append("Django default X-Frame-Options header")

    if django_score >= 3:
        return DetectionResult(
            platform_type   = "django",
            display_name    = "Django (Python)",
            confidence      = min(1.0, django_score / 8),
            signals         = signals,
            inject_strategy = "ssh_custom",
            credential_hint = "Server SSH Credentials",
            credential_guide= [
                {"step": "1", "instruction": "You need SSH access to the server running your Django app"},
                {"step": "2", "instruction": "Enter your server's hostname or IP address below"},
                {"step": "3", "instruction": "Enter your SSH username"},
                {"step": "4", "instruction": "Enter your SSH password or paste your private key"},
                {"step": "5", "instruction": "The engine will install a Django receiver app automatically and disconnect."},
            ],
        )

    # ── Express / Node.js ─────────────────────────────────────────────────────
    express_score = 0
    if "express" in headers.get("x-powered-by", ""):
        express_score += 4
        signals.append("x-powered-by: Express header")
    if "node" in headers.get("server", ""):
        express_score += 2
        signals.append("Node.js server header")

    if express_score >= 3:
        return DetectionResult(
            platform_type   = "express",
            display_name    = "Node.js / Express",
            confidence      = min(1.0, express_score / 6),
            signals         = signals,
            inject_strategy = "ssh_custom",
            credential_hint = "Server SSH Credentials",
            credential_guide= [
                {"step": "1", "instruction": "You need SSH access to your Node.js server"},
                {"step": "2", "instruction": "Enter your server hostname or IP address below"},
                {"step": "3", "instruction": "Enter your SSH username"},
                {"step": "4", "instruction": "Enter your SSH password or paste your private key"},
                {"step": "5", "instruction": "The engine will install an Express middleware receiver automatically and disconnect."},
            ],
        )

    # ── Headless CMS fingerprints ─────────────────────────────────────────────
    if "contentful" in html or "ctfassets.net" in html:
        return DetectionResult(
            platform_type   = "headless_cms",
            display_name    = "Contentful (Headless CMS)",
            confidence      = 0.9,
            signals         = ["Contentful CDN assets detected"],
            inject_strategy = "headless_cms",
            credential_hint = "Contentful Content Management API Key",
            credential_guide= [
                {"step": "1", "instruction": "Log into app.contentful.com"},
                {"step": "2", "instruction": "Go to Settings → API Keys → Content management tokens"},
                {"step": "3", "instruction": "Generate a new token and paste it below. Also provide your Space ID."},
            ],
        )

    if "strapi" in html or "_strapi" in html:
        return DetectionResult(
            platform_type   = "headless_cms",
            display_name    = "Strapi (Headless CMS)",
            confidence      = 0.85,
            signals         = ["Strapi CMS detected"],
            inject_strategy = "headless_cms",
            credential_hint = "Strapi API Token",
            credential_guide= [
                {"step": "1", "instruction": "Log into your Strapi admin panel"},
                {"step": "2", "instruction": "Go to Settings → API Tokens → Create new API Token"},
                {"step": "3", "instruction": "Set type to 'Full Access', copy the token and paste it below. Also provide your Strapi URL."},
            ],
        )

    # ── Unknown / Custom — fall back to SSH deep scan ─────────────────────────
    return _unknown(signals if signals else ["No known platform fingerprints detected"])


def _unknown(signals: list[str]) -> DetectionResult:
    return DetectionResult(
        platform_type   = "unknown",
        display_name    = "Custom / Unknown Backend",
        confidence      = 0.0,
        signals         = signals,
        inject_strategy = "ssh_custom",
        credential_hint = "Server SSH Credentials",
        credential_guide= [
            {"step": "1", "instruction": "Your website uses a custom or unrecognised backend. The engine will connect via SSH, study your server's architecture automatically, and install the right receiver. You do not need to know what your backend uses."},
            {"step": "2", "instruction": "Enter your server's hostname or IP address below"},
            {"step": "3", "instruction": "Enter your SSH username (e.g. ubuntu, root, or your username)"},
            {"step": "4", "instruction": "Enter your SSH password or paste your private SSH key"},
        ],
    )
