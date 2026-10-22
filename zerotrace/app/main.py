from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, contextmanager
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.getenv("ZT_DATA_DIR", str(PROJECT_DIR / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("ZT_DATABASE_PATH", str(DATA_DIR / "zerotrace.sqlite3"))).resolve()
LOCAL_TZ = ZoneInfo("Asia/Kolkata")
SESSION_COOKIE = "zt_session"
SESSION_DAYS = 14
PASSWORD_ITERATIONS = 600_000
MAX_COUNTER_VALUE = 500
MAX_BATCH_TOTAL = 1_000
RETENTION_DAYS = max(90, min(3650, int(os.getenv("ZT_RETENTION_DAYS", "365"))))
COOKIE_SECURE = os.getenv("ZT_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"}
ZT_APP_ORIGIN = os.getenv("ZT_APP_ORIGIN", "").strip()
_RATE_SECRET = os.getenv("ZT_RATE_LIMIT_SECRET", "")
_RATE_SECRET_BYTES = _RATE_SECRET.encode() if _RATE_SECRET else secrets.token_bytes(32)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    _init_db()
    yield


app = FastAPI(
    title="ZeroTrace API",
    version="1.0.0",
    description="Privacy-first aggregate website analytics with registered origins and owner accounts.",
    lifespan=lifespan,
)

# Origins are dynamically enrolled by site owners. CORS alone is not treated as
# authorization: /api/aggregate independently verifies the exact registered Origin.
# Credentials are not exposed cross-origin; dashboard auth is same-origin cookie based.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=r"https?://[^/]+",
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
    max_age=600,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def analytics_body_size_limit(request: Request, call_next):
    # Reject oversized declared bodies before FastAPI/Pydantic parses them. A
    # reverse proxy should also enforce a body-size limit for chunked requests.
    if request.url.path == "/api/aggregate" and request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                too_large = int(content_length) > 32_768
            except ValueError:
                return JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)
            if too_large:
                return JSONResponse({"detail": "Request body is too large."}, status_code=413)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PageViews(StrictModel):
    home: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)
    events: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)
    projects: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)
    registration: int = Field(default=0, alias="register", ge=0, le=MAX_COUNTER_VALUE)


class SectionViews(StrictModel):
    home: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)
    events: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)
    projects: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)
    registration: int = Field(default=0, alias="register", ge=0, le=MAX_COUNTER_VALUE)
    about: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)


class DeviceViews(StrictModel):
    mobile: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)
    desktop: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)
    tablet: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)


class FormFunnel(StrictModel):
    registration_started: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)
    registration_details_reached: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)
    registration_submitted: int = Field(default=0, ge=0, le=MAX_COUNTER_VALUE)


class Metrics(StrictModel):
    page_views: PageViews = Field(default_factory=PageViews)
    section_views: SectionViews = Field(default_factory=SectionViews)
    device_page_views: DeviceViews = Field(default_factory=DeviceViews)
    form_funnel: FormFunnel = Field(default_factory=FormFunnel)


class AggregatePayload(StrictModel):
    site_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    metrics: Metrics


class RegisterPayload(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address.")
        return value


class LoginPayload(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class SiteCreatePayload(StrictModel):
    name: str = Field(min_length=2, max_length=80)
    origin: str = Field(min_length=8, max_length=300)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()


# Rate-limit state is intentionally process-memory only. IP-derived keys are
# HMACed with a process secret and expire from memory; raw IP addresses are not
# written to the database or application logs.
_rate_windows: dict[str, deque[float]] = defaultdict(deque)


def _empty_day() -> dict[str, dict[str, int]]:
    return {
        "page_views": {"home": 0, "events": 0, "projects": 0, "register": 0},
        "section_views": {"home": 0, "events": 0, "projects": 0, "register": 0, "about": 0},
        "device_page_views": {"mobile": 0, "desktop": 0, "tablet": 0},
        "form_funnel": {
            "registration_started": 0,
            "registration_details_reached": 0,
            "registration_submitted": 0,
        },
    }


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DB_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 15000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat(timespec="seconds")


def _retention_cutoff() -> str:
    return (datetime.now(LOCAL_TZ).date() - timedelta(days=RETENTION_DAYS - 1)).isoformat()


def normalize_origin(value: str) -> str:
    """Normalize an origin and reject paths, credentials, query strings, and fragments."""
    try:
        parts = urlsplit(value.strip())
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise ValueError
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError
        if parts.path not in {"", "/"}:
            raise ValueError
        hostname = parts.hostname.encode("idna").decode("ascii").lower()
        port = parts.port
        if port and not ((parts.scheme.lower() == "https" and port == 443) or (parts.scheme.lower() == "http" and port == 80)):
            host = f"{hostname}:{port}"
        else:
            host = hostname
        if ":" in hostname and not hostname.startswith("["):
            host = f"[{hostname}]" + (f":{port}" if port and port not in {80, 443} else "")
        return f"{parts.scheme.lower()}://{host}"
    except (ValueError, UnicodeError, AttributeError):
        raise HTTPException(status_code=422, detail="Enter an origin only, for example https://example.org (no path).")


def _request_origin(request: Request) -> str:
    if ZT_APP_ORIGIN:
        return normalize_origin(ZT_APP_ORIGIN)
    return normalize_origin(f"{request.url.scheme}://{request.url.netloc}")


def _require_same_app_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(status_code=403, detail="Origin header required for browser account actions.")
    try:
        normalized = normalize_origin(origin)
    except HTTPException:
        raise HTTPException(status_code=403, detail="Invalid request origin.")
    if not hmac.compare_digest(normalized, _request_origin(request)):
        raise HTTPException(status_code=403, detail="Cross-origin account action rejected.")


def _is_local_host(host: str) -> bool:
    return host.lower() in {"localhost", "127.0.0.1", "::1", "[::1]"}


def _validate_deployable_origin(origin: str) -> str:
    normalized = normalize_origin(origin)
    parsed = urlsplit(normalized)
    if parsed.scheme != "https" and not _is_local_host(parsed.hostname or ""):
        raise HTTPException(status_code=422, detail="Public websites must use HTTPS. HTTP is allowed only for localhost development.")
    return normalized


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
            CREATE TABLE IF NOT EXISTS sites (
                site_id TEXT PRIMARY KEY,
                owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                origin TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sites_owner ON sites(owner_id);
            CREATE TABLE IF NOT EXISTS daily_aggregates (
                site_id TEXT NOT NULL REFERENCES sites(site_id) ON DELETE CASCADE,
                day TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (site_id, day)
            );
            """
        )
        # An unowned demo site is claimed by the first account. It is never an
        # open, unauthenticated dashboard; aggregate ingestion still checks Origin.
        conn.execute(
            "INSERT OR IGNORE INTO sites(site_id, owner_id, name, origin, created_at) VALUES(?, NULL, ?, ?, ?)",
            ("demo", "Riverside Robotics Club", ZT_APP_ORIGIN and normalize_origin(ZT_APP_ORIGIN) or "http://127.0.0.1:8000", _now_iso()),
        )
        conn.execute("DELETE FROM daily_aggregates WHERE day < ?", (_retention_cutoff(),))


def _password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PASSWORD_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(derived).decode("ascii"),
    )


def _verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_text, digest_text = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(36)
    now = _now_utc()
    expires = (now + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(timespec="seconds"),))
    conn.execute(
        "INSERT INTO sessions(token_hash, user_id, expires_at, created_at) VALUES(?, ?, ?, ?)",
        (_token_digest(token), user_id, expires, now.isoformat(timespec="seconds")),
    )
    return token


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    secure_cookie = COOKIE_SECURE or request.url.scheme.lower() == "https" or ZT_APP_ORIGIN.lower().startswith("https://")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=secure_cookie,
        samesite="lax",
        path="/",
    )


def _client_digest(request: Request) -> str:
    address = request.client.host if request.client else "unknown"
    return hmac.new(_RATE_SECRET_BYTES, address.encode("utf-8"), hashlib.sha256).hexdigest()


def _rate_limit(request: Request, key: str, limit: int, window_seconds: int) -> None:
    now = time.monotonic()
    bucket = _rate_windows[key]
    cutoff = now - window_seconds
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(1, int(window_seconds - (now - bucket[0])))
        raise HTTPException(status_code=429, detail="Too many requests. Please try again later.", headers={"Retry-After": str(retry_after)})
    bucket.append(now)
    # Prevent a long-lived process from collecting stale limiter keys forever.
    if len(_rate_windows) > 20_000:
        for old_key in list(_rate_windows)[:5_000]:
            old_bucket = _rate_windows[old_key]
            while old_bucket and old_bucket[0] < now - 3600:
                old_bucket.popleft()
            if not old_bucket:
                _rate_windows.pop(old_key, None)


def _merge_counts(target: dict[str, dict[str, int]], incoming: dict[str, dict[str, int]]) -> None:
    for group, values in incoming.items():
        for key, value in values.items():
            target[group][key] += int(value)


def _metrics_from_payload(metrics: Metrics) -> dict[str, dict[str, int]]:
    data = metrics.model_dump(by_alias=True)
    total = sum(value for group in data.values() for value in group.values())
    if total > MAX_BATCH_TOTAL:
        raise HTTPException(status_code=413, detail="Aggregate batch exceeds the allowed total.")
    return data


def _site_for_owner(site_id: str, owner_id: int) -> sqlite3.Row:
    with _db() as conn:
        row = conn.execute("SELECT site_id, name, origin, created_at FROM sites WHERE site_id=? AND owner_id=?", (site_id, owner_id)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Site not found.")
    return row


def get_current_user(request: Request) -> dict[str, Any]:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    now = _now_utc().isoformat(timespec="seconds")
    with _db() as conn:
        row = conn.execute(
            "SELECT users.id, users.email FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires_at>?",
            (_token_digest(raw_token), now),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Session expired. Sign in again.")
    return {"id": int(row["id"]), "email": row["email"]}


def _plain(metric_value: int) -> dict[str, Any]:
    # Every count is returned, including 0–4. There is no small-count suppression.
    return {"value": int(metric_value), "suppressed": False}


def _date_range(days: int) -> list[date]:
    today = datetime.now(LOCAL_TZ).date()
    return [today - timedelta(days=offset) for offset in reversed(range(days))]


def _sum_metrics(rows: list[dict[str, dict[str, int]]]) -> dict[str, dict[str, int]]:
    totals = _empty_day()
    for row in rows:
        _merge_counts(totals, row)
    return totals


def _load_daily(site_id: str, days: int) -> dict[str, dict[str, dict[str, int]]]:
    dates = _date_range(days)
    start = dates[0].isoformat()
    end = dates[-1].isoformat()
    with _db() as conn:
        conn.execute("DELETE FROM daily_aggregates WHERE day < ?", (_retention_cutoff(),))
        rows = conn.execute(
            "SELECT day, metrics_json FROM daily_aggregates WHERE site_id=? AND day BETWEEN ? AND ?",
            (site_id, start, end),
        ).fetchall()
    result = {row["day"]: json.loads(row["metrics_json"]) for row in rows}
    return result


def _owner_sites(owner_id: int) -> list[dict[str, Any]]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT site_id, name, origin, created_at FROM sites WHERE owner_id=? ORDER BY created_at ASC",
            (owner_id,),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/", response_class=HTMLResponse)
def dashboard_home(request: Request) -> Response:
    if not request.cookies.get(SESSION_COOKIE):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request) -> Response:
    return dashboard_home(request)


@app.get("/login", response_class=HTMLResponse)
def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "auth.html")


@app.get("/demo", response_class=HTMLResponse)
def demo_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "demo" / "index.html")


@app.get("/sdk/zerotrace.js")
def sdk_file() -> FileResponse:
    return FileResponse(STATIC_DIR / "zerotrace.js", media_type="application/javascript", headers={"Cache-Control": "public, max-age=300"})


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "ZeroTrace", "storage": "SQLite daily aggregates", "version": "1.0.0"}


@app.post("/api/auth/register")
def register(payload: RegisterPayload, request: Request, response: Response) -> dict[str, Any]:
    _require_same_app_origin(request)
    _rate_limit(request, f"register:{_client_digest(request)}", 8, 3600)
    if len(payload.password) < 10:
        raise HTTPException(status_code=422, detail="Password must contain at least 10 characters.")
    with _db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users(email, password_hash, created_at) VALUES(?, ?, ?)",
                (payload.email, _password_hash(payload.password), _now_iso()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        user_id = int(cur.lastrowid)
        demo = conn.execute("SELECT owner_id FROM sites WHERE site_id='demo'").fetchone()
        if demo is not None and demo["owner_id"] is None:
            origin = _request_origin(request)
            conn.execute("UPDATE sites SET owner_id=?, origin=? WHERE site_id='demo'", (user_id, origin))
            conn.execute("DELETE FROM daily_aggregates WHERE site_id='demo'")
        token = _create_session(conn, user_id)
    _set_session_cookie(response, token, request)
    return {"ok": True, "user": {"id": user_id, "email": payload.email}}


@app.post("/api/auth/login")
def login(payload: LoginPayload, request: Request, response: Response) -> dict[str, Any]:
    _require_same_app_origin(request)
    _rate_limit(request, f"login:{_client_digest(request)}", 12, 600)
    with _db() as conn:
        user = conn.execute("SELECT id, email, password_hash FROM users WHERE email=?", (payload.email,)).fetchone()
        # Verify against a fixed dummy hash for unknown users to reduce timing differences.
        if user is None:
            _verify_password(payload.password, _DUMMY_PASSWORD_HASH)
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")
        if not _verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")
        token = _create_session(conn, int(user["id"]))
    _set_session_cookie(response, token, request)
    return {"ok": True, "user": {"id": int(user["id"]), "email": user["email"]}}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    _require_same_app_origin(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with _db() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash=?", (_token_digest(token),))
    secure_cookie = COOKIE_SECURE or request.url.scheme.lower() == "https" or ZT_APP_ORIGIN.lower().startswith("https://")
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure_cookie, httponly=True, samesite="lax")
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"ok": True, "user": user}


@app.get("/api/sites")
def list_sites(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    return {"sites": _owner_sites(user["id"])}


@app.post("/api/sites")
def create_site(payload: SiteCreatePayload, request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    _require_same_app_origin(request)
    _rate_limit(request, f"create-site:{user['id']}", 20, 3600)
    origin = _validate_deployable_origin(payload.origin)
    site_id = "zt_" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]
    with _db() as conn:
        try:
            conn.execute(
                "INSERT INTO sites(site_id, owner_id, name, origin, created_at) VALUES(?, ?, ?, ?, ?)",
                (site_id, user["id"], payload.name, origin, _now_iso()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="This origin is already registered. Use one ZeroTrace site per origin.")
    return {"ok": True, "site": {"site_id": site_id, "name": payload.name, "origin": origin}}


@app.delete("/api/sites/{site_id}")
def delete_site(site_id: str, request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, bool]:
    _require_same_app_origin(request)
    if site_id == "demo":
        raise HTTPException(status_code=400, detail="The built-in demo site cannot be deleted; its aggregate data can be reset.")
    with _db() as conn:
        result = conn.execute("DELETE FROM sites WHERE site_id=? AND owner_id=?", (site_id, user["id"]))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Site not found.")
    return {"ok": True}


@app.post("/api/aggregate")
def receive_aggregate(payload: AggregatePayload, request: Request) -> dict[str, Any]:
    """Validate origin, enforce limits, and merge counters directly into a daily SQLite bucket."""
    if request.headers.get("content-length"):
        try:
            if int(request.headers["content-length"]) > 32_768:
                raise HTTPException(status_code=413, detail="Request body is too large.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.")
    origin_header = request.headers.get("origin")
    if not origin_header:
        raise HTTPException(status_code=403, detail="A registered Origin is required for analytics ingestion.")
    try:
        origin = normalize_origin(origin_header)
    except HTTPException:
        raise HTTPException(status_code=403, detail="Invalid request origin.")
    with _db() as conn:
        site = conn.execute("SELECT site_id, origin FROM sites WHERE site_id=?", (payload.site_id,)).fetchone()
    if site is None:
        raise HTTPException(status_code=404, detail="Unknown site ID.")
    if not hmac.compare_digest(origin, site["origin"]):
        raise HTTPException(status_code=403, detail="This Origin is not registered for the supplied site ID.")

    # Anonymous ingestion abuse controls. The source IP is HMACed only in process
    # memory for throttling; the raw address/digest is never persisted or logged.
    source_digest = _client_digest(request)
    _rate_limit(request, f"ingest-source:{payload.site_id}:{source_digest}", 120, 60)
    _rate_limit(request, f"ingest-site:{payload.site_id}", 6000, 60)

    incoming = _metrics_from_payload(payload.metrics)
    if not any(value for group in incoming.values() for value in group.values()):
        return {"ok": True, "accepted": False, "message": "Empty batch ignored."}

    day_key = datetime.now(LOCAL_TZ).date().isoformat()
    with _db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT metrics_json FROM daily_aggregates WHERE site_id=? AND day=?",
            (payload.site_id, day_key),
        ).fetchone()
        current = json.loads(existing["metrics_json"]) if existing else _empty_day()
        _merge_counts(current, incoming)
        conn.execute(
            "INSERT INTO daily_aggregates(site_id, day, metrics_json, updated_at) VALUES(?, ?, ?, ?) "
            "ON CONFLICT(site_id, day) DO UPDATE SET metrics_json=excluded.metrics_json, updated_at=excluded.updated_at",
            (payload.site_id, day_key, json.dumps(current, separators=(",", ":")), _now_iso()),
        )
        conn.execute("DELETE FROM daily_aggregates WHERE day < ?", (_retention_cutoff(),))
    return {"ok": True, "accepted": True, "storage": "persistent daily aggregates only"}


@app.get("/api/dashboard")
def dashboard_data(
    site_id: str = Query(min_length=1, max_length=64),
    days: int = Query(default=30, ge=7, le=90),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    site = _site_for_owner(site_id, user["id"])
    all_days = _load_daily(site_id, days)
    wanted_dates = _date_range(days)
    rows = [all_days.get(day.isoformat(), _empty_day()) for day in wanted_dates]
    totals = _sum_metrics(rows)
    today_key = datetime.now(LOCAL_TZ).date().isoformat()
    today = all_days.get(today_key, _empty_day())

    series = []
    for day, row in zip(wanted_dates, rows):
        series.append({
            "date": day.isoformat(),
            "label": day.strftime("%d %b"),
            "page_views": sum(row["page_views"].values()),
            "suppressed": False,
        })

    page_total = sum(totals["page_views"].values())
    device_total = sum(totals["device_page_views"].values())
    form_starts = totals["form_funnel"]["registration_started"]
    form_submits = totals["form_funnel"]["registration_submitted"]
    conversion = round((form_submits / form_starts) * 100, 1) if form_starts else None

    def grouped_report(group: str) -> dict[str, dict[str, Any]]:
        return {key: _plain(value) for key, value in totals[group].items()}

    return {
        "site_id": site_id,
        "site_name": site["name"],
        "origin": site["origin"],
        "period_days": days,
        "timezone": "Asia/Kolkata",
        "summary": {
            "page_views": _plain(page_total),
            "today_page_views": _plain(sum(today["page_views"].values())),
            "form_starts": _plain(form_starts),
            "form_submissions": _plain(form_submits),
            "form_conversion_percent": conversion,
            "device_page_views": _plain(device_total),
        },
        "page_views": grouped_report("page_views"),
        "section_views": grouped_report("section_views"),
        "device_page_views": grouped_report("device_page_views"),
        "form_funnel": grouped_report("form_funnel"),
        "series": series,
        "privacy": {
            "persistent_visitor_ids": False,
            "sdk_cookies": False,
            "sdk_browser_storage": False,
            "individual_event_history_stored": False,
            "raw_submissions_persisted": False,
            "persistent_daily_aggregate_buckets": True,
            "ip_addresses_persisted": False,
            "ip_derived_rate_limit_key_transient_in_memory": True,
            "formal_differential_privacy": False,
            "small_count_suppression": False,
        },
    }


@app.delete("/api/sites/{site_id}/aggregates")
def reset_site_aggregates(site_id: str, request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    _require_same_app_origin(request)
    _site_for_owner(site_id, user["id"])
    with _db() as conn:
        conn.execute("DELETE FROM daily_aggregates WHERE site_id=?", (site_id,))
    return {"ok": True, "message": "Aggregate history cleared for this site."}


# A dummy hash keeps unknown-email login attempts closer in cost to existing users.
_DUMMY_PASSWORD_HASH = _password_hash("not-a-real-password-for-timing-only")
_init_db()
