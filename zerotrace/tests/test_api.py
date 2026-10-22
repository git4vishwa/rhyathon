from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture(autouse=True)
def isolated_database(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DB_PATH", tmp_path / "zerotrace-test.sqlite3")
    main._rate_windows.clear()
    main._init_db()
    yield
    main._rate_windows.clear()


@pytest.fixture
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def register(client: TestClient, email="owner@example.org", password="correct-horse-battery"):
    response = client.post(
        "/api/auth/register",
        headers={"Origin": "http://testserver"},
        json={"email": email, "password": password},
    )
    return response


def sample_payload(site_id="demo", page_views=None):
    page_views = page_views or {"home": 2}
    return {
        "site_id": site_id,
        "metrics": {
            "page_views": page_views,
            "section_views": {"home": 1},
            "device_page_views": {"mobile": 2},
            "form_funnel": {"registration_started": 1},
        },
    }


def test_health_and_login_page(client):
    assert client.get("/health").json()["ok"] is True
    assert client.get("/login").status_code == 200
    assert client.get("/", follow_redirects=False).status_code == 303


def test_register_signs_in_and_claims_demo_site(client):
    response = register(client)
    assert response.status_code == 200, response.text
    assert "zt_session" in response.cookies
    assert client.get("/api/auth/me").json()["user"]["email"] == "owner@example.org"
    sites = client.get("/api/sites").json()["sites"]
    assert len(sites) == 1
    assert sites[0]["site_id"] == "demo"
    assert sites[0]["origin"] == "http://testserver"


def test_small_counts_are_visible_and_aggregates_persist(client):
    assert register(client).status_code == 200
    first = client.post("/api/aggregate", headers={"Origin": "http://testserver"}, json=sample_payload())
    assert first.status_code == 200, first.text
    data = client.get("/api/dashboard?site_id=demo&days=7").json()
    assert data["summary"]["page_views"] == {"value": 2, "suppressed": False}
    assert data["page_views"]["home"]["value"] == 2
    assert data["series"][-1]["page_views"] == 2
    assert data["privacy"]["small_count_suppression"] is False
    # Re-reading through a new SQLite connection returns the aggregate from disk.
    assert main._load_daily("demo", 7)[data["series"][-1]["date"]]["page_views"]["home"] == 2


def test_unapproved_fields_are_rejected(client):
    assert register(client).status_code == 200
    response = client.post(
        "/api/aggregate",
        headers={"Origin": "http://testserver"},
        json={"site_id": "demo", "visitor_id": "not-allowed", "metrics": {"page_views": {"home": 1}}},
    )
    assert response.status_code == 422


def test_origin_mismatch_is_rejected(client):
    assert register(client).status_code == 200
    response = client.post(
        "/api/aggregate",
        headers={"Origin": "https://attacker.example"},
        json=sample_payload(),
    )
    assert response.status_code == 403


def test_origin_is_required_for_ingestion(client):
    assert register(client).status_code == 200
    response = client.post("/api/aggregate", json=sample_payload())
    assert response.status_code == 403


def test_site_creation_requires_auth_and_https_for_public_origins(client):
    no_auth = client.post(
        "/api/sites", headers={"Origin": "http://testserver"},
        json={"name": "Example", "origin": "https://example.org"},
    )
    assert no_auth.status_code == 401
    assert register(client).status_code == 200
    bad_http = client.post(
        "/api/sites", headers={"Origin": "http://testserver"},
        json={"name": "Example site", "origin": "http://example.org"},
    )
    assert bad_http.status_code == 422
    ok = client.post(
        "/api/sites", headers={"Origin": "http://testserver"},
        json={"name": "Example site", "origin": "https://example.org"},
    )
    assert ok.status_code == 200, ok.text
    site = ok.json()["site"]
    assert site["origin"] == "https://example.org"
    assert site["site_id"].startswith("zt_")

    ingested = client.post(
        "/api/aggregate", headers={"Origin": "https://example.org"},
        json=sample_payload(site["site_id"], {"events": 1}),
    )
    assert ingested.status_code == 200, ingested.text
    report = client.get(f"/api/dashboard?site_id={site['site_id']}&days=7").json()
    assert report["summary"]["page_views"]["value"] == 1


def test_accounts_cannot_view_other_owners_sites(client):
    assert register(client, "one@example.org").status_code == 200
    client.post("/api/auth/logout", headers={"Origin": "http://testserver"})
    assert register(client, "two@example.org").status_code == 200
    response = client.get("/api/dashboard?site_id=demo&days=7")
    assert response.status_code == 404


def test_logout_invalidates_session(client):
    assert register(client).status_code == 200
    response = client.post("/api/auth/logout", headers={"Origin": "http://testserver"})
    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_unknown_site_and_malformed_origin_are_rejected(client):
    assert register(client).status_code == 200
    response = client.post(
        "/api/aggregate", headers={"Origin": "http://testserver"},
        json=sample_payload("unknown-site", {"home": 1}),
    )
    assert response.status_code == 404


def test_dashboard_and_demo_pages_are_served(client):
    assert client.get("/demo").status_code == 200
    assert client.get("/sdk/zerotrace.js").status_code == 200


def test_login_restores_session_after_logout(client):
    assert register(client).status_code == 200
    assert client.post("/api/auth/logout", headers={"Origin": "http://testserver"}).status_code == 200
    response = client.post(
        "/api/auth/login",
        headers={"Origin": "http://testserver"},
        json={"email": "owner@example.org", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    assert client.get("/api/auth/me").status_code == 200


def test_account_mutation_rejects_cross_origin_requests(client):
    assert register(client).status_code == 200
    response = client.post(
        "/api/sites",
        headers={"Origin": "https://evil.example"},
        json={"name": "Bad site", "origin": "https://example.org"},
    )
    assert response.status_code == 403


def test_oversized_ingestion_body_is_rejected_before_parsing(client):
    response = client.post(
        "/api/aggregate",
        headers={"Origin": "http://testserver", "Content-Length": "40000", "Content-Type": "application/json"},
        content=b"{}",
    )
    assert response.status_code == 413
