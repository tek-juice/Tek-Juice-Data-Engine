#!/usr/bin/env python3
"""
DATA ENGINE — Spec Merger CLI
==============================
Fetches all service /openapi.json endpoints, merges them, and writes the
result to docs/openapi_merged.yaml (and optionally .json).

Use this to:
  - Import the full API into Postman / Insomnia
  - Publish a static API reference
  - Feed into API linters or contract tests

Usage
------
  # Default: write docs/openapi_merged.yaml
  python docs/merge_specs.py

  # Also write JSON
  python docs/merge_specs.py --json

  # Custom output path
  python docs/merge_specs.py --out /tmp/data_engine_api.yaml

  # Quiet (no per-service status lines)
  python docs/merge_specs.py --quiet

Requirements
-------------
  pip install httpx pyyaml     (already in requirements.txt)

How it connects
----------------
merge_specs.py calls the same _fetch_all + _merge_specs logic that lives in
swagger_server.py.  To avoid duplication it imports those functions directly.
If the docs server is already running on :8099, it fetches the merged spec
from there instead (faster, and avoids re-fetching all services).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# ── Allow running as a script from the repo root ─────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


async def _try_docs_server(url: str = "http://localhost:8099/openapi.json") -> dict | None:
    """Try to grab the already-merged spec from the running docs server."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=4) as client:
            r = await client.get(url)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return None


async def main(args: argparse.Namespace) -> None:
    import yaml

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    log = (lambda *a: None) if args.quiet else print  # noqa: E731

    # ── Try docs server shortcut first ───────────────────────────────────────
    log("Checking if docs server is running on :8099…")
    spec = await _try_docs_server()
    if spec:
        log("✓  Docs server is running — using pre-merged spec (fast path)")
    else:
        log("  Docs server not running — fetching specs directly from all services…")
        # Import the server module's fetch + merge logic
        from docs.swagger_server import _fetch_all, _merge_specs
        await _fetch_all()
        spec = _merge_specs()

    # ── Write YAML ────────────────────────────────────────────────────────────
    yaml_content = yaml.dump(spec, allow_unicode=True, sort_keys=False, default_flow_style=False)
    out_path.write_text(yaml_content, encoding="utf-8")
    log(f"\n✓  YAML written → {out_path}  ({out_path.stat().st_size / 1024:.1f} KB)")

    # ── Optionally write JSON ─────────────────────────────────────────────────
    if args.json:
        json_path = out_path.with_suffix(".json")
        json_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"✓  JSON written → {json_path}  ({json_path.stat().st_size / 1024:.1f} KB)")

    # ── Summary ───────────────────────────────────────────────────────────────
    if not args.quiet:
        paths  = len(spec.get("paths", {}))
        schemas = len(spec.get("components", {}).get("schemas", {}))
        print(f"\n   Paths:   {paths}")
        print(f"   Schemas: {schemas}")
        print(f"   Version: {spec.get('info', {}).get('version', 'unknown')}")
        print()
        print("Import into Postman:")
        print("  Postman → Import → Upload Files → select openapi_merged.yaml")
        print()
        print("Import into Insomnia:")
        print("  Insomnia → Import → From File → select openapi_merged.yaml")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Merge all DATA ENGINE service OpenAPI specs into one YAML file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "openapi_merged.yaml"),
        help="Output file path (default: docs/openapi_merged.yaml)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also write a .json version alongside the YAML",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress status output",
    )
    asyncio.run(main(parser.parse_args()))
