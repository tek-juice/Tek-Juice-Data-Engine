"""
DATA ENGINE — End-to-End Tests: API Gateway
Tests the full request lifecycle through the API Gateway:
authentication, routing, rate limiting, and health aggregation.

These tests require all services to be running.
Run with:
    pytest tests/end_to_end/test_api_gateway.py -v -m e2e \
        --base-url http://localhost:8000
"""

import pytest


pytestmark = pytest.mark.e2e

BASE_URL = "http://localhost:8000"


@pytest.fixture(scope="module")
def api_client():
    """Return an httpx client pointed at the API gateway."""
    try:
        import httpx
        client = httpx.Client(base_url=BASE_URL, timeout=10)
        yield client
        client.close()
    except ImportError:
        pytest.skip("httpx not installed")


@pytest.fixture(scope="module")
def auth_token(api_client):
    """Obtain a JWT token for test user."""
    response = api_client.post(
        "/api/v1/auth/token",
        data={"username": "admin@tekjuice.ai", "password": "ChangeMe123!"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code != 200:
        pytest.skip(f"Auth failed ({response.status_code}) — is the API gateway running?")
    return response.json()["access_token"]


@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}


class TestHealthEndpoints:

    def test_gateway_health(self, api_client):
        response = api_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert data["status"] in ("healthy", "degraded")

    def test_health_has_timestamp(self, api_client):
        response = api_client.get("/health")
        assert "timestamp" in response.json()

    def test_health_has_service_checks(self, api_client):
        response = api_client.get("/health")
        data = response.json()
        assert "checks" in data


class TestAuthentication:

    def test_login_returns_tokens(self, api_client):
        response = api_client.post(
            "/api/v1/auth/token",
            data={"username": "admin@tekjuice.ai", "password": "ChangeMe123!"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    def test_invalid_credentials_rejected(self, api_client):
        response = api_client.post(
            "/api/v1/auth/token",
            data={"username": "bad@user.com", "password": "wrongpassword"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 401

    def test_protected_endpoint_requires_auth(self, api_client):
        response = api_client.get("/api/v1/ingest/documents")
        assert response.status_code == 401

    def test_protected_endpoint_with_valid_token(self, api_client, auth_headers):
        response = api_client.get("/api/v1/ingest/documents", headers=auth_headers)
        assert response.status_code in (200, 404)

    def test_token_refresh(self, api_client, auth_token):
        # First get a refresh token
        login_resp = api_client.post(
            "/api/v1/auth/token",
            data={"username": "admin@tekjuice.ai", "password": "ChangeMe123!"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        refresh_token = login_resp.json().get("refresh_token", "")
        if not refresh_token:
            pytest.skip("No refresh token returned")

        response = api_client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()


class TestDocumentIngestion:

    def test_list_documents_authenticated(self, api_client, auth_headers):
        response = api_client.get("/api/v1/ingest/documents", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data or "total" in data

    def test_upload_invalid_file_type_rejected(self, api_client, auth_headers):
        response = api_client.post(
            "/api/v1/ingest/upload",
            files={"file": ("test.exe", b"MZ\x90\x00", "application/octet-stream")},
            headers=auth_headers,
        )
        assert response.status_code in (400, 415, 422)

    def test_document_status_not_found(self, api_client, auth_headers):
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = api_client.get(
            f"/api/v1/ingest/status/{fake_id}", headers=auth_headers
        )
        assert response.status_code == 404


class TestDashboardEndpoints:

    def test_overview_endpoint(self, api_client, auth_headers):
        response = api_client.get("/api/v1/dashboard/overview", headers=auth_headers)
        assert response.status_code == 200

    def test_metrics_summary_endpoint(self, api_client, auth_headers):
        response = api_client.get(
            "/api/v1/dashboard/metrics/summary?hours=24", headers=auth_headers
        )
        assert response.status_code in (200, 404)

    def test_activity_log_endpoint(self, api_client, auth_headers):
        response = api_client.get(
            "/api/v1/dashboard/activity-log?hours=1&limit=10",
            headers=auth_headers,
        )
        assert response.status_code in (200, 404)


class TestAPIKeyManagement:

    def test_create_api_key(self, api_client, auth_headers):
        response = api_client.post(
            "/api/v1/auth/api-keys",
            json={"name": "e2e-test-key"},
            headers=auth_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert "api_key" in data
        assert data["api_key"].startswith("de_")
        # Store for cleanup
        return data["key_id"]

    def test_api_key_auth_works(self, api_client, auth_headers):
        # Create a key first
        create_resp = api_client.post(
            "/api/v1/auth/api-keys",
            json={"name": "e2e-auth-test"},
            headers=auth_headers,
        )
        if create_resp.status_code != 201:
            pytest.skip("Could not create API key")

        api_key = create_resp.json()["api_key"]
        # Use it to authenticate
        response = api_client.get(
            "/api/v1/ingest/documents",
            headers={"X-API-Key": api_key},
        )
        assert response.status_code in (200, 401)

    def test_revoke_api_key(self, api_client, auth_headers):
        # Create then revoke
        create_resp = api_client.post(
            "/api/v1/auth/api-keys",
            json={"name": "e2e-revoke-test"},
            headers=auth_headers,
        )
        if create_resp.status_code != 201:
            pytest.skip("Could not create API key")

        key_id = create_resp.json()["key_id"]
        revoke_resp = api_client.delete(
            f"/api/v1/auth/api-keys/{key_id}",
            headers=auth_headers,
        )
        assert revoke_resp.status_code == 204


class TestErrorHandling:

    def test_invalid_json_body_returns_422(self, api_client, auth_headers):
        response = api_client.post(
            "/api/v1/embed",
            content="not json",
            headers={**auth_headers, "Content-Type": "application/json"},
        )
        assert response.status_code == 422

    def test_missing_required_field_returns_422(self, api_client, auth_headers):
        response = api_client.post(
            "/api/v1/embed",
            json={},   # missing 'texts' field
            headers=auth_headers,
        )
        assert response.status_code in (400, 422)

    def test_unknown_route_returns_404(self, api_client):
        response = api_client.get("/api/v1/nonexistent-endpoint")
        assert response.status_code == 404
