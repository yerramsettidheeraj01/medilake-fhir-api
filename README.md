# MediLake mock FHIR API

Three hospitals (H1, H2, H3), FHIR R4 `Patient`/`Encounter`/`Observation`, ~50M historical rows
(computed on the fly, nothing stored) plus "live" records that keep appearing after the cut-off
(2026-09-30). OAuth2 client-credentials, one token URL per hospital, 429 + Retry-After rate limits.

## Run locally
    pip install -r requirements.txt
    uvicorn app.main:app --reload
    curl -s -X POST localhost:8000/H1/oauth/token -d grant_type=client_credentials \
         -d client_id=h1-client -d client_secret=h1-secret
    curl -H "Authorization: Bearer <token>" \
         "localhost:8000/H1/r4/Encounter?_lastUpdated=gt2026-09-29T00:00:00Z&_count=5&_sort=_lastUpdated"
Add `&_summary=count` to get a total (handy for reconciliation). Docs UI: /docs.

## Deploy free (Render)
1. Push this folder to a GitHub repo. 2. Render > New > Blueprint > pick the repo (uses render.yaml).
3. Set PUBLIC_URL to your service URL. 4. Open the service Environment tab to read the generated
H1/H2/H3 client secrets (client ids: h1-client, h2-client, h3-client). Free tier sleeps after ~15 min idle;
open /health before a pipeline run (the Lambda/Step Functions retries also cover a cold start).

## Plug into the lakehouse project
- hospitals.json: fhir_base_url = https://<app>/H1/r4 (same for H2, H3); keep page_size/rate_limit_rps.
- Secrets (create_secrets.sh): token_url = https://<app>/H1/oauth/token, client_id, client_secret.
- Historical data: `python scripts/generate_exports.py --mappings <repo>/config/mappings.json --out exports`
  (use SCALE=0.01 for a 500k-row dev run), then `bash scripts/upload_historical.sh H1 exports/H1`.
- Env knobs: SCALE, HIST_CUTOFF (keep equal to your initial_watermark + 1 day), RATE_LIMIT=0 to disable 429s.
