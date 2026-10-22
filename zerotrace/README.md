# ZeroTrace — privacy-first aggregate website analytics

**Tagline:** Analytics without the trail.

ZeroTrace is a web application with an embeddable JavaScript SDK and Python/FastAPI backend. Website owners create an account, register an exact site origin, install a small script, and view page-category counts, coarse device breakdowns, section views, and form-funnel totals.

The SDK does not create persistent visitor IDs, set analytics cookies, use browser storage, fingerprint visitors, or send a per-click event stream. It holds counters in page memory and submits them in batches. The backend validates the registered site ID and browser `Origin`, then merges increments into **persistent daily aggregate counters** in SQLite. It does not save each batch or an individual event history.

## What changed in this version

- Owner registration, sign-in, sign-out, and persistent server-side sessions.
- Site registration with an exact, normalized origin. Public website origins must use HTTPS; HTTP is allowed for localhost development.
- A site-specific SDK snippet generated in the dashboard.
- Origin validation at ingestion, strict payload schemas, bounded counter increments, request-size limits, and rate limiting.
- SQLite persistence for daily aggregate counters (default retention is 365 days).
- Counts of all sizes are returned and displayed. **There is no minimum-five-visit suppression rule.** This is a deliberate product choice, not an anonymity guarantee; small counts can reveal information in sensitive contexts.
- The SDK still avoids visitor IDs and client-side persistence. For abuse protection, the API creates a process-memory HMAC from the source connection address and uses it for short-window rate limiting. Neither the raw IP nor that digest is written to SQLite or application logs. Network infrastructure can still observe connection metadata.

## Run on Windows

1. Install Python 3.11 or later. During setup, enable **Add Python to PATH**.
2. Extract this project into a folder you can edit.
3. Double-click `run_windows.bat`, or open PowerShell in the project root and run:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --log-level warning
   ```

   If PowerShell blocks activation, use `\.venv\Scripts\python.exe -m pip install -r requirements.txt` and `\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --log-level warning`. The `tzdata` dependency is included for Windows timezone support.
4. Open **http://127.0.0.1:8000/login**.
5. Create an account. The first account claims the built-in demo website. Subsequent accounts can add their own site origins.
6. Open **http://127.0.0.1:8000/demo** and interact with the page and its demo form. Use fictitious form values only.
7. The SDK batches counters every eight seconds. You can also select **Send aggregate batch** on the demo page. Return to the dashboard to see the updated numbers.

## Run on macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --log-level warning
```

Then visit `http://127.0.0.1:8000/login`.

## Publish a live demo on Railway (recommended for this SQLite version)

The repository includes a `Dockerfile` configured to listen on the hosting provider's `PORT`. The hosted app must use persistent storage because account sessions, site registrations, and daily aggregate counts are stored in SQLite. Railway supports a mounted volume; attach it before relying on the hosted data. Hosting quotas and charges can change, so review the current plan and usage limits before deploying.

### 1. Push the project to GitHub

1. Extract the `ZeroTrace_MVP.zip` archive.
2. Create a GitHub repository, for example `zerotrace`.
3. Upload the **contents of the `zerotrace` folder** to the repository root. `Dockerfile`, `requirements.txt`, and the `app` directory should be at the top level. Do not upload `.env` files or the local `data` database.

### 2. Deploy from GitHub

1. Sign in to [Railway](https://railway.com/) and connect GitHub.
2. Create a project and select **Deploy from GitHub repo**, then choose your ZeroTrace repository.
3. Railway will detect the `Dockerfile` and build the service. Wait for the first deployment to finish.
4. In the service's **Settings → Networking**, choose **Generate Domain**. Copy the resulting HTTPS URL, such as `https://your-service.up.railway.app`. It will be your evaluator-facing website.

### 3. Attach persistent storage

1. In the Railway project, add a volume to the ZeroTrace service.
2. Set its mount path to `/data`.
3. In the service's **Variables** panel, set `ZT_DATABASE_PATH` to `/data/zerotrace.sqlite3`.

Without this volume, SQLite data can be lost when a container is replaced or redeployed. Keep the service to a single replica because SQLite and the current in-memory rate limiter are designed for this small single-instance prototype.

### 4. Set production environment variables

Add these variables in Railway's **Variables** panel. Replace the example origin with the exact HTTPS domain Railway generated, with no trailing slash or path.

```text
ZT_APP_ORIGIN=https://your-service.up.railway.app
ZT_COOKIE_SECURE=true
ZT_DATABASE_PATH=/data/zerotrace.sqlite3
ZT_RETENTION_DAYS=365
ZT_RATE_LIMIT_SECRET=PASTE_A_NEW_RANDOM_SECRET_HERE
```

Generate a high-entropy secret locally with Python:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy the output into `ZT_RATE_LIMIT_SECRET`. Do not commit secrets to GitHub. Once variables are saved, redeploy/restart the service if Railway does not do so automatically. Make sure `ZT_APP_ORIGIN` exactly matches the public HTTPS origin before creating your first owner account; this is used for same-origin account protection and secure session-cookie configuration.

### 5. Test the live service

1. Open `https://your-service.up.railway.app/health`. It should return a JSON response with `"ok": true`.
2. Open `https://your-service.up.railway.app/login` and create your owner account. Use a strong password of at least 10 characters.
3. Open `/demo` to exercise the built-in sample website, then return to the dashboard.
4. In the dashboard, register the real public website origin that you control, such as `https://club.example.org`, and copy the generated SDK snippet into that site's pages.
5. Test ingestion from that exact origin. The SDK's `data-endpoint` should point to `https://your-service.up.railway.app/api/aggregate`.
6. Give evaluators the public base URL and, if the dashboard is part of their evaluation, an evaluation account or a supervised walkthrough. Do not share your personal account credentials publicly.

### Important demo and security notes

- A public domain lets evaluators open ZeroTrace in a browser; they do not need to install Python.
- The default login page protects the dashboard. Evaluators will not see your private dashboard unless they sign in to an account you choose to provide or you demonstrate it yourself.
- Do not deploy any real personal/sensitive website data for a hackathon demo. Small aggregate counts are intentionally visible.
- Exact `Origin` validation is useful for normal browser embedding but is not cryptographic authentication; non-browser scripts can forge the header. Current rate limiting is in memory, and does not defend against a distributed attacker.
- A persistent SQLite volume is suitable for a small demo. Before production use, add backups, monitoring, secure account recovery, email verification or equivalent account controls, stronger distributed abuse protection, and a professional security review.

## Register and install a real website

1. Deploy ZeroTrace to a server you control and put it behind HTTPS. Use a persistent writable disk/volume for the SQLite database.
2. Set the deployment variables below before starting the server.
3. Open `/login`, create an account, then choose **Add website** in the dashboard.
4. Enter the website name and its **origin only**. Examples: `https://example.org`, `https://www.example.org`. Do not include a path, query, or fragment. Each exact origin can be registered once.
5. Select the website and click **Get install snippet**. Add the generated `<script>` tag to the website pages you want to measure.
6. For classic multi-page websites, add `data-zt-page="home"` to the page's `<body>` (supported values: `home`, `events`, `projects`, `register`). For single-page / hash navigation, mark navigation links with `data-zt-page="events"` etc. The built-in demo uses hash links.
7. Add `data-zt-section="events"` to sections you want counted for visibility. Add `data-zt-form="registration"` to a form whose milestones should be counted. Use `data-zt-form-stage="details"` on the control that advances to the details stage.
8. Test from the exact registered origin. The API rejects missing origins and origins that do not match the site's registration.

Example snippet (use the real snippet generated in the dashboard):

```html
<script
  src="https://YOUR-ZEROTRACE-HOST/sdk/zerotrace.js"
  data-site="YOUR_SITE_ID"
  data-endpoint="https://YOUR-ZEROTRACE-HOST/api/aggregate"
  defer
></script>
```

### Deployment environment variables

- `ZT_APP_ORIGIN` — canonical origin of the ZeroTrace dashboard, e.g. `https://analytics.example.org`. Set this when deployed behind a reverse proxy so same-origin protection is not dependent on internal proxy URLs.
- `ZT_COOKIE_SECURE=true` — required for HTTPS deployment so the session cookie is marked Secure.
- `ZT_DATABASE_PATH` — optional explicit SQLite file path. Default: `data/zerotrace.sqlite3` inside the project directory.
- `ZT_DATA_DIR` — optional data directory used when `ZT_DATABASE_PATH` is not set.
- `ZT_RETENTION_DAYS` — optional daily aggregate retention, between 90 and 3650 days; default 365.
- `ZT_RATE_LIMIT_SECRET` — optional high-entropy secret for HMACing transient source addresses used by the in-memory rate limiter. Set a random secret in deployments and keep it stable across workers if applicable. If unset, a per-process random key is generated and rate-limit identities do not survive restart.

Do not publish the service with the default development configuration. Use TLS, a persistent protected database volume, secrets management, backups, monitoring, a reverse proxy with size/time limits, and a reviewed deployment configuration. Run a security review before handling production traffic.

## API endpoints

| Method | Endpoint | Authentication | Purpose |
|---|---|---|---|
| `GET` | `/health` | Public | Health/version response |
| `GET` | `/login` | Public | Sign-in/create-account page |
| `POST` | `/api/auth/register` | Public, rate-limited | Create owner account and sign in |
| `POST` | `/api/auth/login` | Public, rate-limited | Sign in |
| `POST` | `/api/auth/logout` | Session | Revoke current session |
| `GET` | `/api/auth/me` | Session | Current owner account |
| `GET` | `/api/sites` | Session | List sites belonging to the owner |
| `POST` | `/api/sites` | Session | Register site name and origin |
| `DELETE` | `/api/sites/{site_id}` | Session | Remove owned website and its aggregates (the built-in demo cannot be removed) |
| `POST` | `/api/aggregate` | Public ingestion; registered `Origin` required | Validate and merge a metrics batch |
| `GET` | `/api/dashboard?site_id=...&days=30` | Session; ownership required | Read the selected site's aggregates |
| `DELETE` | `/api/sites/{site_id}/aggregates` | Session; ownership required | Delete selected site's aggregate history |
| `GET` | `/sdk/zerotrace.js` | Public | Serve SDK |
| `GET` | `/demo` | Public | Instrumented demo site |

Account-changing POSTs require a same-app `Origin` header. Owner dashboard APIs use an HttpOnly, SameSite=Lax session cookie; session tokens are stored hashed in SQLite. Passwords are hashed with PBKDF2-HMAC-SHA256. This MVP does not implement email verification, password reset, multi-factor authentication, or team roles.

## What is retained

SQLite contains:

- User email and password hash, for account access.
- Hashed session tokens and expiration times.
- Registered website name and exact origin.
- One JSON counter object per website per calendar day, with aggregate metric totals only.

SQLite does **not** contain a row per analytics batch, page event, click, session, visitor, or form submission. The dashboard API displays small counts rather than suppressing them. Deleting a site's aggregate history removes its daily counters; removing a non-demo site deletes its site record and associated aggregate history.

The rate limiter uses a digest of the source connection address in process memory to limit repeated requests. This is not saved as analytics data, but it is still transient processing of connection metadata. Reverse proxies, hosting providers, or network layers may independently log addresses; configure those layers appropriately.

## Threat model and limitations

- Exact `Origin` checks are useful for browser embedding and stop a normal browser page on an unregistered origin from submitting accepted analytics. They are **not cryptographic authentication**: a non-browser attacker can forge an `Origin` header. Rate limits and payload bounds reduce abuse but do not eliminate distributed counter manipulation.
- Site IDs are identifiers included in the script snippet, not secrets. Dashboard data and administration require an owner session.
- There is no formal differential privacy, secure aggregation, or mathematical anonymity guarantee. Counts of 1–4 are visible by design; for sensitive content, choose metrics that do not expose sensitive small groups or add a separately designed privacy mechanism.
- Data is stored persistently by site/day; the SDK itself still keeps pending counters in page memory only. Analytics batches can be counted twice if a server commits a batch but the client never receives the successful response and retries it. This prototype uses at-least-once delivery, not deduplicated exactly-once ingestion.
- The application disables Uvicorn access logs in the supplied run commands, but production infrastructure must be reviewed separately.

## Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The tests cover account creation/login, owner-only access, site registration, public HTTPS-origin validation, origin mismatch rejection, persistent aggregation, low-count visibility, and strict rejection of identifying payload fields.

## Resetting data

Use **Clear selected site data** in the dashboard to delete the chosen site's aggregate history. This cannot be undone. For the built-in demo only, the site registration remains so the SDK can continue submitting new counts.
