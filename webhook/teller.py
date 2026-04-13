"""Teller Connect enrollment — re-enrollment page and token save API."""

import json
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

from shared.db import get_session

router = APIRouter()

TELLER_APP_ID = os.environ.get("TELLER_APP_ID", "")
TELLER_WEBHOOK_KEY = os.environ.get("TELLER_WEBHOOK_KEY", "")


def _get_enrollment_id() -> str | None:
    """Get existing enrollment ID from tokens table or accounts table."""
    with get_session() as session:
        # Try tokens table first
        row = session.execute(
            text("SELECT enrollment_id FROM teller.tokens WHERE id = 1")
        ).fetchone()
        if row and row[0]:
            return row[0]

        # Fall back to accounts table
        row = session.execute(
            text("SELECT DISTINCT enrollment_id FROM teller.accounts WHERE enrollment_id IS NOT NULL LIMIT 1")
        ).fetchone()
        return row[0] if row else None


@router.get("/teller/enroll")
def teller_enroll():
    """Serve the Teller Connect enrollment page."""
    if not TELLER_APP_ID:
        raise HTTPException(500, "TELLER_APP_ID must be set")

    enrollment_id = _get_enrollment_id()
    # Safe JS interpolation via json.dumps
    app_id_js = json.dumps(TELLER_APP_ID)
    enrollment_id_js = json.dumps(enrollment_id)
    api_key_js = json.dumps(TELLER_WEBHOOK_KEY)

    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Basin — Teller Connect</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='0' y2='1'%3E%3Cstop offset='0%25' stop-color='%2338bdf8'/%3E%3Cstop offset='100%25' stop-color='%232563eb'/%3E%3C/linearGradient%3E%3C/defs%3E%3Ccircle cx='16' cy='16' r='15' fill='url(%23g)'/%3E%3Cpath d='M16 6c-4 0-7 3-7 5 0 3 2.5 4.5 4 6s2 3.5 3 6c1-2.5 1.5-4.5 3-6s4-3 4-6c0-2-3-5-7-5z' fill='white' opacity='0.9'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&family=Space+Mono:wght@400&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{
    --base: #0a0f1a;
    --surface: #141b2d;
    --border: #1e293b;
    --text-primary: #f1f5f9;
    --text-secondary: #94a3b8;
    --accent: #10b981;
    --accent-glow: rgba(16, 185, 129, 0.15);
    --alert: #ef4444;
    --mono: 'Space Mono', monospace;
    --sans: 'DM Sans', sans-serif;
  }}
  body {{
    font-family: var(--sans);
    background: var(--base);
    color: var(--text-primary);
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    -webkit-font-smoothing: antialiased;
  }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 2.5rem;
    max-width: 420px;
    width: 100%;
    text-align: center;
  }}
  h1 {{
    font-size: 1.25rem;
    font-weight: 600;
    margin-bottom: 0.5rem;
  }}
  .subtitle {{
    color: var(--text-secondary);
    font-size: 0.875rem;
    margin-bottom: 1.5rem;
  }}
  .status {{
    font-family: var(--mono);
    font-size: 0.8rem;
    padding: 0.75rem 1rem;
    border-radius: 8px;
    margin-top: 1rem;
  }}
  .status.waiting {{
    background: rgba(59, 130, 246, 0.1);
    color: #60a5fa;
    border: 1px solid rgba(59, 130, 246, 0.2);
  }}
  .status.success {{
    background: var(--accent-glow);
    color: var(--accent);
    border: 1px solid rgba(16, 185, 129, 0.3);
  }}
  .status.error {{
    background: rgba(239, 68, 68, 0.1);
    color: var(--alert);
    border: 1px solid rgba(239, 68, 68, 0.2);
  }}
  button {{
    font-family: var(--sans);
    font-weight: 500;
    font-size: 0.875rem;
    padding: 0.625rem 1.25rem;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-primary);
    cursor: pointer;
    margin-top: 1rem;
  }}
  button:hover {{ background: #1a2340; }}
  .hidden {{ display: none; }}
  .enrollment-info {{
    font-family: var(--mono);
    font-size: 0.7rem;
    color: var(--text-secondary);
    margin-top: 1.5rem;
    opacity: 0.6;
  }}
</style>
</head>
<body>
<div class="card">
  <h1>Teller Connect</h1>
  <p class="subtitle" id="subtitle">Link your bank account to Basin</p>
  <div id="status" class="status waiting">Opening Teller Connect...</div>
  <button id="retryBtn" class="hidden" onclick="openConnect()">Try Again</button>
  <div class="enrollment-info" id="enrollInfo"></div>
</div>

<script src="https://cdn.teller.io/connect/connect.js"></script>
<script>
  var appId = {app_id_js};
  var enrollmentId = {enrollment_id_js};
  var apiKey = {api_key_js};

  if (enrollmentId) {{
    document.getElementById("enrollInfo").textContent = "Re-enrolling: " + enrollmentId;
    document.getElementById("subtitle").textContent = "Re-authenticate your bank connection";
  }}

  function setStatus(text, cls) {{
    var el = document.getElementById("status");
    el.textContent = text;
    el.className = "status " + cls;
  }}

  function openConnect() {{
    document.getElementById("retryBtn").classList.add("hidden");
    setStatus("Opening Teller Connect...", "waiting");

    var opts = {{
      applicationId: appId,
      products: ["transactions"],
      onSuccess: function(enrollment) {{
        setStatus("Saving token...", "waiting");
        var headers = {{"Content-Type": "application/json"}};
        if (apiKey) headers["X-API-Key"] = apiKey;
        fetch("/api/teller/token", {{
          method: "POST",
          headers: headers,
          body: JSON.stringify({{
            access_token: enrollment.accessToken,
            enrollment_id: enrollment.enrollment.id
          }})
        }})
        .then(function(resp) {{ return resp.json(); }})
        .then(function(data) {{
          if (data.status === "saved") {{
            setStatus("Token saved. Collector will use it on next run.", "success");
            document.getElementById("subtitle").textContent = "Bank connected successfully";
          }} else {{
            setStatus("Unexpected response: " + JSON.stringify(data), "error");
            document.getElementById("retryBtn").classList.remove("hidden");
          }}
        }})
        .catch(function(err) {{
          setStatus("Failed to save token: " + err, "error");
          document.getElementById("retryBtn").classList.remove("hidden");
        }});
      }},
      onFailure: function(failure) {{
        setStatus("Connection failed: " + (failure.message || "unknown error"), "error");
        document.getElementById("retryBtn").classList.remove("hidden");
      }},
      onExit: function() {{
        var statusEl = document.getElementById("status");
        if (!statusEl.classList.contains("success")) {{
          setStatus("Teller Connect closed. Click to retry.", "error");
          document.getElementById("retryBtn").classList.remove("hidden");
        }}
      }}
    }};

    if (enrollmentId) {{
      opts.enrollmentId = enrollmentId;
    }}

    var handler = TellerConnect.setup(opts);
    handler.open();
  }}

  openConnect();
</script>
</body>
</html>""")


@router.post("/api/teller/token")
async def save_teller_token(request: Request):
    """Save a new Teller access token from the enrollment flow."""
    if TELLER_WEBHOOK_KEY:
        api_key = request.headers.get("X-API-Key", "")
        if api_key != TELLER_WEBHOOK_KEY:
            return JSONResponse(status_code=401, content={"error": "invalid api key"})

    body = await request.json()
    access_token = body.get("access_token", "")
    enrollment_id = body.get("enrollment_id")

    if not access_token:
        raise HTTPException(400, "access_token is required")

    with get_session() as session:
        session.execute(text("""
            INSERT INTO teller.tokens (id, access_token, enrollment_id, updated_at)
            VALUES (1, :token, :eid, now())
            ON CONFLICT (id) DO UPDATE SET
                access_token = :token,
                enrollment_id = :eid,
                updated_at = now()
        """), {"token": access_token, "eid": enrollment_id})

    return {"status": "saved"}
