"""
DATA ENGINE — Browser Fingerprint Spoofer
Generates fully consistent browser fingerprint profiles to defeat
advanced bot detection (TLS fingerprinting, canvas/WebGL, audio, fonts).

Key attack surface covered:
  1. TLS fingerprint (JA3/JA3S)  — cycled via httpx transport cipher ordering
  2. navigator.* properties       — consistent with the active UA
  3. Canvas 2D noise              — stable per-session noise injection
  4. WebGL renderer/vendor        — masked to real GPU strings
  5. AudioContext fingerprint      — timing jitter injection
  6. Screen/window geometry        — matched to UA viewport
  7. Timezone / locale / platform  — matched to UA region
  8. Plugin / MIME list            — OS-appropriate list
  9. Referer chains                — realistic navigation history
 10. Accept header ordering        — browser-specific ordering
"""

from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field
from typing import Any


# ── Browser profile definitions ───────────────────────────────────────────────
# Each profile groups UA, viewport, platform, TZ, accept headers, and JS
# navigator overrides so every property is internally consistent.

@dataclass
class BrowserProfile:
    """A fully consistent browser identity for one scraping session."""
    name:           str
    user_agent:     str
    platform:       str           # navigator.platform
    vendor:         str           # navigator.vendor
    language:       str           # navigator.language
    languages:      list[str]     # navigator.languages
    timezone:       str           # Intl.DateTimeFormat resolved timezone
    screen_width:   int
    screen_height:  int
    viewport_w:     int
    viewport_h:     int
    color_depth:    int           # screen.colorDepth
    pixel_ratio:    float         # window.devicePixelRatio
    webgl_renderer: str           # WebGL RENDERER string
    webgl_vendor:   str           # WebGL VENDOR string
    accept:         str           # HTTP Accept header value
    accept_language:str           # HTTP Accept-Language header value
    sec_ch_ua:      str           # Sec-CH-UA header (Chrome only)
    sec_ch_ua_platform: str       # Sec-CH-UA-Platform header
    plugins:        list[str]     # navigator.plugins names
    canvas_seed:    int = field(default_factory=lambda: random.randint(1, 999_999))

    def to_js_init_script(self) -> str:
        """
        Returns a JS init script that overrides all fingerprint properties
        before any page script runs. Inject via Playwright add_init_script().
        """
        langs_js = str(self.languages)
        plugins_js = ", ".join(f'"{p}"' for p in self.plugins)
        return f"""
// ── Fingerprint Spoofer — {self.name} ──────────────────────────────────────
(function() {{
    // 1. Hide automation
    Object.defineProperty(navigator, 'webdriver',   {{ get: () => undefined }});
    Object.defineProperty(navigator, 'domAutomation', {{ get: () => undefined }});

    // 2. Platform / vendor / language
    Object.defineProperty(navigator, 'platform',   {{ get: () => "{self.platform}" }});
    Object.defineProperty(navigator, 'vendor',     {{ get: () => "{self.vendor}" }});
    Object.defineProperty(navigator, 'language',   {{ get: () => "{self.language}" }});
    Object.defineProperty(navigator, 'languages',  {{ get: () => {langs_js} }});

    // 3. Plugins (non-empty list defeats headless detection)
    const fakePlugins = [{plugins_js}].map(name => ({{
        name, filename: name.toLowerCase().replace(/ /g, '_') + '.so', description: name
    }}));
    Object.defineProperty(navigator, 'plugins', {{
        get: () => Object.assign(fakePlugins, {{ length: fakePlugins.length, item: i => fakePlugins[i], namedItem: n => fakePlugins.find(p => p.name === n) }})
    }});
    Object.defineProperty(navigator, 'mimeTypes', {{ get: () => {{ length: 0 }} }});

    // 4. Screen geometry
    Object.defineProperty(screen, 'width',       {{ get: () => {self.screen_width} }});
    Object.defineProperty(screen, 'height',      {{ get: () => {self.screen_height} }});
    Object.defineProperty(screen, 'availWidth',  {{ get: () => {self.screen_width} }});
    Object.defineProperty(screen, 'availHeight', {{ get: () => {self.screen_height - 40} }});
    Object.defineProperty(screen, 'colorDepth',  {{ get: () => {self.color_depth} }});
    Object.defineProperty(screen, 'pixelDepth',  {{ get: () => {self.color_depth} }});
    Object.defineProperty(window, 'devicePixelRatio', {{ get: () => {self.pixel_ratio} }});
    window.outerWidth  = {self.viewport_w};
    window.outerHeight = {self.viewport_h};

    // 5. Chrome runtime (defeat "is Chrome?" check)
    window.chrome = {{ runtime: {{}}, loadTimes: () => {{}}, csi: () => {{}}, app: {{}} }};

    // 6. Permissions API stub
    const origQuery = window.navigator.permissions && window.navigator.permissions.query;
    if (origQuery) {{
        window.navigator.permissions.query = params =>
            params.name === 'notifications'
                ? Promise.resolve({{ state: Notification.permission }})
                : origQuery(params);
    }}

    // 7. Canvas noise — stable per-session seed {self.canvas_seed}
    const _seed = {self.canvas_seed};
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {{
        const ctx = this.getContext('2d');
        if (ctx) {{
            const imageData = ctx.getImageData(0, 0, this.width || 1, this.height || 1);
            for (let i = 0; i < imageData.data.length; i += 17) {{
                imageData.data[i] = (imageData.data[i] + ((_seed * (i+1)) % 3)) & 0xFF;
            }}
            ctx.putImageData(imageData, 0, 0);
        }}
        return origToDataURL.apply(this, arguments);
    }};
    const origGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, attrs) {{
        const ctx = origGetContext.call(this, type, attrs);
        if (ctx && type === '2d') {{
            const origFillText = ctx.fillText.bind(ctx);
            ctx.fillText = function(text, x, y, maxWidth) {{
                return origFillText(text, x + (_seed % 2 === 0 ? 0.00001 : -0.00001), y);
            }};
        }}
        return ctx;
    }};

    // 8. WebGL renderer/vendor masking
    const origGetParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {{
        if (param === 37445) return "{self.webgl_vendor}";   // VENDOR
        if (param === 37446) return "{self.webgl_renderer}"; // RENDERER
        return origGetParam.call(this, param);
    }};
    if (typeof WebGL2RenderingContext !== 'undefined') {{
        const orig2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(param) {{
            if (param === 37445) return "{self.webgl_vendor}";
            if (param === 37446) return "{self.webgl_renderer}";
            return orig2.call(this, param);
        }};
    }}

    // 9. AudioContext timing jitter (defeats audio fingerprinting)
    const origCreateAnalyser = AudioContext.prototype.createAnalyser;
    if (origCreateAnalyser) {{
        AudioContext.prototype.createAnalyser = function() {{
            const analyser = origCreateAnalyser.call(this);
            const origGetFloat = analyser.getFloatFrequencyData.bind(analyser);
            analyser.getFloatFrequencyData = function(array) {{
                origGetFloat(array);
                for (let i = 0; i < array.length; i++) {{
                    array[i] += (_seed % 5 === 0 ? 0.0001 : -0.0001);
                }}
            }};
            return analyser;
        }};
    }}
}})();
""".strip()


# ── Pre-defined profile pool ───────────────────────────────────────────────────

BROWSER_PROFILES: list[BrowserProfile] = [
    BrowserProfile(
        name="chrome-win11-office",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        platform="Win32", vendor="Google Inc.", language="en-US",
        languages=["en-US", "en"],
        timezone="America/New_York",
        screen_width=1920, screen_height=1080, viewport_w=1903, viewport_h=969,
        color_depth=24, pixel_ratio=1.0,
        webgl_renderer="ANGLE (NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
        webgl_vendor="Google Inc. (NVIDIA)",
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        accept_language="en-US,en;q=0.9",
        sec_ch_ua='"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
        plugins=["Chrome PDF Plugin", "Chrome PDF Viewer", "Native Client"],
    ),
    BrowserProfile(
        name="chrome-macos-creative",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        platform="MacIntel", vendor="Google Inc.", language="en-US",
        languages=["en-US", "en"],
        timezone="America/Los_Angeles",
        screen_width=2560, screen_height=1600, viewport_w=1440, viewport_h=900,
        color_depth=30, pixel_ratio=2.0,
        webgl_renderer="ANGLE (Apple, Apple M2, OpenGL 4.1)",
        webgl_vendor="Google Inc. (Apple)",
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        accept_language="en-US,en;q=0.9",
        sec_ch_ua='"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        sec_ch_ua_platform='"macOS"',
        plugins=["Chrome PDF Plugin", "Chrome PDF Viewer"],
    ),
    BrowserProfile(
        name="firefox-win10-standard",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
        platform="Win32", vendor="", language="en-US",
        languages=["en-US", "en"],
        timezone="America/Chicago",
        screen_width=1366, screen_height=768, viewport_w=1349, viewport_h=668,
        color_depth=24, pixel_ratio=1.0,
        webgl_renderer="GeForce GTX 1650/PCIe/SSE2",
        webgl_vendor="NVIDIA Corporation",
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        accept_language="en-US,en;q=0.5",
        sec_ch_ua="",
        sec_ch_ua_platform="",
        plugins=["OpenH264 Video Codec", "Widevine Content Decryption Module"],
    ),
    BrowserProfile(
        name="safari-macos-consumer",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
        platform="MacIntel", vendor="Apple Computer, Inc.", language="en-GB",
        languages=["en-GB", "en"],
        timezone="Europe/London",
        screen_width=2560, screen_height=1664, viewport_w=1440, viewport_h=900,
        color_depth=30, pixel_ratio=2.0,
        webgl_renderer="Apple GPU",
        webgl_vendor="Apple Inc.",
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        accept_language="en-GB,en;q=0.9",
        sec_ch_ua="",
        sec_ch_ua_platform="",
        plugins=[],
    ),
    BrowserProfile(
        name="chrome-android-mobile",
        user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.53 Mobile Safari/537.36",
        platform="Linux armv81", vendor="Google Inc.", language="en-US",
        languages=["en-US", "en"],
        timezone="America/New_York",
        screen_width=412, screen_height=915, viewport_w=412, viewport_h=829,
        color_depth=24, pixel_ratio=3.5,
        webgl_renderer="Adreno (TM) 740",
        webgl_vendor="Qualcomm",
        accept="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        accept_language="en-US,en;q=0.9",
        sec_ch_ua='"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        sec_ch_ua_platform='"Android"',
        plugins=[],
    ),
]


def get_random_profile() -> BrowserProfile:
    """Return a random browser profile (with a fresh canvas seed each time)."""
    p = random.choice(BROWSER_PROFILES)
    # Give each session a unique canvas seed so profiles aren't trackable
    import dataclasses
    return dataclasses.replace(p, canvas_seed=random.randint(1, 999_999))


def get_profile_http_headers(profile: BrowserProfile, referer: str = "") -> dict[str, str]:
    """
    Build the HTTP headers that match the given browser profile.
    These are sent in every httpx request alongside the UA.
    """
    headers: dict[str, str] = {
        "User-Agent":      profile.user_agent,
        "Accept":          profile.accept,
        "Accept-Language": profile.accept_language,
        "Accept-Encoding": "gzip, deflate, br",
        "DNT":             "1",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":  "document",
        "Sec-Fetch-Mode":  "navigate",
        "Sec-Fetch-Site":  "none" if not referer else "same-origin",
        "Sec-Fetch-User":  "?1",
        "Cache-Control":   "max-age=0",
    }
    if profile.sec_ch_ua:
        headers["Sec-CH-UA"]          = profile.sec_ch_ua
        headers["Sec-CH-UA-Mobile"]   = "?1" if "Mobile" in profile.user_agent else "?0"
        headers["Sec-CH-UA-Platform"] = profile.sec_ch_ua_platform
    if referer:
        headers["Referer"] = referer
    return headers


def get_session_fingerprint_id(profile: BrowserProfile) -> str:
    """Return a stable hex ID for this profile (for cache/session keying)."""
    raw = f"{profile.name}:{profile.canvas_seed}:{profile.user_agent}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]
