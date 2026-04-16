## MP Build Tracker (FastAPI)

Production-ready FastAPI service for internal automations. Currently exposes:

- **GET** `/health`
- **POST** `/jobs/MPBuildTracker` (protected by `X-Webhook-Secret`)

### Run locally

1) Create a virtualenv and install deps:

```bash
cd mp-build-tracker-service
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) Set environment variables:

- **Option A (recommended locally)**: copy `.env.example` to `.env` and edit `WEBHOOK_SECRET`
- **Option B**: export it in your shell:

```bash
export WEBHOOK_SECRET="your-secret"
```

3) Start the server:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Test with curl

Health:

```bash
curl -sS http://localhost:8000/health
```

Webhook (sample payload):

```bash
curl -sS -X POST "http://localhost:8000/jobs/MPBuildTracker" \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: your-secret" \
  -d '{
    "run_date": "2026-04-16",
    "source": "zapier",
    "rows": [
      {
        "home_code": "HC123",
        "onboard_date": "2026-04-15",
        "booking_url": "",
        "mp_active": false,
        "sync_ready": true,
        "photos_ready": true
      }
    ]
  }'
```

Unauthorized example (should return HTTP 401):

```bash
curl -i -X POST "http://localhost:8000/jobs/MPBuildTracker" \
  -H "Content-Type: application/json" \
  -d '{"run_date":"2026-04-16","source":"zapier","rows":[]}'
```

### Deploy to Render

1) Create a new Render **Web Service** from this repo.
2) Render will read `render.yaml` automatically (Blueprint).
3) In Render, set the secret env var:
   - **WEBHOOK_SECRET**: a strong shared secret (do not commit it)
4) Deploy.

### Zapier webhook URL pattern

After deployment, your base URL will look like:

- `https://<your-render-service-name>.onrender.com`

Use this exact endpoint in Zapier:

- `https://<your-render-service-name>.onrender.com/jobs/MPBuildTracker`

Also add a request header in Zapier:

- `X-Webhook-Secret: <your WEBHOOK_SECRET value>`
