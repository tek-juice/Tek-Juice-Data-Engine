"""
DATA ENGINE — Composite Health Check
Runs all service health checks and returns a structured report.
Used by monitoring systems, load balancers, and the k8s readiness probe.

Usage:
    python -m monitoring.healthchecks.healthcheck
    # exits 0 if all healthy, 1 if any critical service is unhealthy
"""

import asyncio
import sys
import json
from datetime import datetime, UTC
from dataclasses import dataclass, field

import httpx


@dataclass
class ServiceStatus:
    name: str
    url: str
    status: str          # healthy | degraded | unreachable
    response_time_ms: float | None = None
    error: str | None = None


@dataclass
class HealthReport:
    overall: str                              # healthy | degraded | critical
    timestamp: str
    services: list[ServiceStatus] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 0 if self.overall == "healthy" else 1


SERVICES = [
    ("api_gateway",       "http://localhost:8000/health"),
    ("ingestion_service", "http://localhost:8001/health"),
    ("embedding_service", "http://localhost:8003/health"),
    ("vector_vault",      "http://localhost:8004/health"),
    ("telemetry_service", "http://localhost:8005/health"),
    ("dashboard_backend", "http://localhost:8011/health"),
    ("seo_engine",        "http://localhost:8012/health"),
    ("geo_engine",        "http://localhost:8013/health"),
]

CRITICAL_SERVICES = {"api_gateway", "vector_vault", "embedding_service"}


async def check_service(name: str, url: str) -> ServiceStatus:
    import time
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(url)
            elapsed = round((time.perf_counter() - start) * 1000, 1)
            status = "healthy" if r.status_code == 200 else "degraded"
            return ServiceStatus(name=name, url=url, status=status, response_time_ms=elapsed)
    except Exception as exc:
        elapsed = round((time.perf_counter() - start) * 1000, 1)
        return ServiceStatus(name=name, url=url, status="unreachable",
                             response_time_ms=elapsed, error=str(exc))


async def run_health_check() -> HealthReport:
    tasks = [check_service(name, url) for name, url in SERVICES]
    statuses = await asyncio.gather(*tasks)

    unhealthy_critical = [
        s for s in statuses
        if s.name in CRITICAL_SERVICES and s.status != "healthy"
    ]
    any_unhealthy = [s for s in statuses if s.status != "healthy"]

    if unhealthy_critical:
        overall = "critical"
    elif any_unhealthy:
        overall = "degraded"
    else:
        overall = "healthy"

    return HealthReport(
        overall=overall,
        timestamp=datetime.now(UTC).isoformat(),
        services=list(statuses),
    )


def main():
    report = asyncio.run(run_health_check())
    output = {
        "overall": report.overall,
        "timestamp": report.timestamp,
        "services": {
            s.name: {
                "status": s.status,
                "response_time_ms": s.response_time_ms,
                "error": s.error,
            }
            for s in report.services
        },
    }
    print(json.dumps(output, indent=2))
    sys.exit(report.exit_code)


if __name__ == "__main__":
    main()
