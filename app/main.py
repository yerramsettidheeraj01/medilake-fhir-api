"""Mock multi-hospital FHIR R4 API with OAuth2 client-credentials (one token URL per hospital)."""
import base64, collections, hmac, os, re, time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode

import jwt
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from . import gen

SECRET = os.getenv("JWT_SECRET", "dev-only-change-me")
TTL = 300
PUBLIC = os.getenv("PUBLIC_URL", "").rstrip("/")
RATE = os.getenv("RATE_LIMIT", "1") == "1"
CLIENTS = {h: (os.getenv(f"{h}_CLIENT_ID", f"{h.lower()}-client"),
               os.getenv(f"{h}_CLIENT_SECRET", f"{h.lower()}-secret")) for h in gen.HOSP}

app = FastAPI(title="MediLake mock FHIR API")


class FhirError(Exception):
    def __init__(self, status, msg, headers=None):
        self.status, self.msg, self.headers = status, msg, headers or {}


@app.exception_handler(FhirError)
async def _err(_, e: FhirError):
    return JSONResponse({"resourceType": "OperationOutcome",
                         "issue": [{"severity": "error", "code": "processing", "diagnostics": e.msg}]},
                        status_code=e.status, headers=e.headers, media_type="application/fhir+json")


def fhir(body): return JSONResponse(body, media_type="application/fhir+json")
def base(request): return PUBLIC or str(request.base_url).rstrip("/")


_hits = collections.defaultdict(collections.deque)
def throttle(key, rps):  # allows 2x the configured rps, then 429 + Retry-After
    now = time.monotonic(); q = _hits[key]
    while q and now - q[0] > 1: q.popleft()
    if len(q) >= rps * 2: raise FhirError(429, "rate limit exceeded", {"Retry-After": "1"})
    q.append(now)


def auth(h: str, request: Request):
    if h not in gen.HOSP: raise FhirError(404, f"unknown hospital {h}")
    a = request.headers.get("authorization", "")
    if not a.startswith("Bearer "):
        raise FhirError(401, "missing bearer token", {"WWW-Authenticate": "Bearer"})
    try: c = jwt.decode(a[7:], SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise FhirError(401, "invalid or expired token", {"WWW-Authenticate": 'Bearer error="invalid_token"'})
    if c.get("hosp") != h: raise FhirError(403, "token is not valid for this hospital")
    if RATE: throttle(c["sub"], gen.HOSP[h]["rps"])
    return c


@app.get("/")
def home(request: Request):
    b, now = base(request), int(time.time())
    return {h: {"name": v["name"], "fhir_base_url": f"{b}/{h}/r4", "token_url": f"{b}/{h}/oauth/token",
                "client_id": CLIENTS[h][0], "rate_limit_rps": v["rps"], "page_size": v["page"],
                "rows_available_now": {r: gen.count_now(h, r, now) for r in gen.RES}}
            for h, v in gen.HOSP.items()}


@app.get("/health")
def health(): return {"ok": True}


@app.post("/{h}/oauth/token")
async def token(h: str, request: Request):
    if h not in gen.HOSP: raise FhirError(404, f"unknown hospital {h}")
    form = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
    cid, sec = form.get("client_id"), form.get("client_secret")
    a = request.headers.get("authorization", "")
    if a.startswith("Basic "):
        cid, _, sec = base64.b64decode(a[6:]).decode().partition(":")
    if form.get("grant_type") != "client_credentials":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    ok_id, ok_sec = CLIENTS[h]
    if not (hmac.compare_digest((cid or "").encode(), ok_id.encode())
            and hmac.compare_digest((sec or "").encode(), ok_sec.encode())):
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    now = int(time.time())
    tok = jwt.encode({"sub": cid, "hosp": h, "scope": "system/*.read", "iat": now, "exp": now + TTL},
                     SECRET, algorithm="HS256")
    return {"access_token": tok, "token_type": "Bearer", "expires_in": TTL, "scope": "system/*.read"}


@app.get("/{h}/r4/metadata")
def metadata(h: str):
    return fhir({"resourceType": "CapabilityStatement", "status": "active", "fhirVersion": "4.0.1",
                 "kind": "instance", "format": ["json"],
                 "rest": [{"mode": "server", "resource": [
                     {"type": t, "interaction": [{"code": "search-type"}, {"code": "read"}],
                      "searchParam": [{"name": "_lastUpdated", "type": "date"}]} for t in gen.FHIR_TYPE]}]})


def parse_ts(v):
    m = re.match(r"^(eq|ne|gt|ge|lt|le|sa|eb|ap)?(.+)$", v)
    op, s = m.group(1) or "eq", m.group(2).strip().replace(" ", "+")
    try: d = datetime.fromisoformat(s)
    except ValueError: raise FhirError(400, f"bad date {s}")
    return op, int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp())


@app.get("/{h}/r4/{rtype}")
def search(h: str, rtype: str, request: Request, _=Depends(auth)):
    r = gen.FHIR_TYPE.get(rtype)
    if not r: raise FhirError(404, f"unsupported resource {rtype}")
    q = request.query_params
    lo, hi = 0, gen.count_now(h, r, int(time.time())) - 1
    for v in q.getlist("_lastUpdated"):
        op, x = parse_ts(v)
        if op in ("gt", "sa"): lo = max(lo, gen.first_idx(h, r, x, True))
        elif op in ("ge", "eq"): lo = max(lo, gen.first_idx(h, r, x, False))
        elif op in ("lt", "eb"): hi = min(hi, gen.first_idx(h, r, x, False) - 1)
        elif op == "le": hi = min(hi, gen.first_idx(h, r, x, True) - 1)
    if q.get("_summary") == "count":
        return fhir({"resourceType": "Bundle", "type": "searchset", "total": max(0, hi - lo + 1)})
    start = max(lo, int(q.get("page_token", 0)))
    count = max(1, min(int(q.get("_count", gen.HOSP[h]["page"])), 1000))
    end = min(hi, start + count - 1)
    b = f"{base(request)}/{h}/r4/{rtype}"
    bundle = {"resourceType": "Bundle", "type": "searchset", "link": [{"relation": "self", "url": b}]}
    if end >= start:
        bundle["entry"] = [{"resource": gen.to_fhir(h, r, i)} for i in range(start, end + 1)]
    if end < hi:
        params = [(k, v) for k, v in q.multi_items() if k != "page_token"] + [("page_token", end + 1)]
        bundle["link"].append({"relation": "next", "url": f"{b}?{urlencode(params)}"})
    return fhir(bundle)


@app.get("/{h}/r4/{rtype}/{rid}")
def read(h: str, rtype: str, rid: str, _=Depends(auth)):
    r = gen.FHIR_TYPE.get(rtype)
    if not r or not rid[1:].isdigit() or rid[0] != "PEO"[gen.RES.index(r)]:
        raise FhirError(404, "not found")
    i = int(rid[1:])
    if i >= gen.count_now(h, r, int(time.time())): raise FhirError(404, "not found")
    return fhir(gen.to_fhir(h, r, i))
