"""Deterministic synthetic EHR data. Any record is computed from (hospital, resource, index),
so 50M rows need no storage and the API and the CSV exports always agree."""
import os
from datetime import date, datetime, timedelta, timezone

M = (1 << 64) - 1
UTC = timezone.utc
START = int(datetime(2023, 1, 1, tzinfo=UTC).timestamp())
T0 = int(datetime.fromisoformat(os.getenv("HIST_CUTOFF", "2026-09-30T00:00:00+00:00")).timestamp())
SCALE = float(os.getenv("SCALE", "1"))  # 0.01 = 500k rows for quick dev runs

HOSP = {
    "H1": dict(name="City General Hospital", rps=5, page=500,
               n=dict(patient=900_000, encounter=4_100_000, observation=17_000_000)),
    "H2": dict(name="Lakeside Medical Center", rps=3, page=250,
               n=dict(patient=700_000, encounter=3_000_000, observation=12_300_000)),
    "H3": dict(name="Riverside Community Hospital", rps=5, page=500,
               n=dict(patient=500_000, encounter=2_200_000, observation=9_300_000)),
}
RES = ("patient", "encounter", "observation")
FHIR_TYPE = {"Patient": "patient", "Encounter": "encounter", "Observation": "observation"}

# Timeline: record i of a table has last_updated = START + i*step, so index order == time order.
# History ends at T0 (the export cut-off); the same density continues after T0 as "live" data.
def n_hist(h, r): return max(1, int(HOSP[h]["n"][r] * SCALE))
def step(h, r): return (T0 - START) / n_hist(h, r)
def t_of(h, r, i): return START + int(i * step(h, r))
def count_now(h, r, now): return int((now - START) / step(h, r)) + 1

def first_idx(h, r, x, strict):
    """First index with last_updated > x (strict) or >= x."""
    i = max(0, int((x - START) / step(h, r)) - 2)
    while (t_of(h, r, i) <= x) if strict else (t_of(h, r, i) < x):
        i += 1
    return i

def h64(*xs):
    x = 0x9E3779B97F4A7C15
    for v in xs:
        x = (x ^ (v & M)) & M
        x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & M
        x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & M
        x ^= x >> 31
    return x

FIRST = ["Aarav", "Vivaan", "Aditya", "Sai", "Arjun", "Ishaan", "Ananya", "Diya", "Isha", "Meera",
         "Priya", "Kavya", "Rohan", "Neha", "Karthik", "Sneha", "Ravi", "Lakshmi", "Imran", "Fatima"]
LAST = ["Reddy", "Sharma", "Patel", "Rao", "Naidu", "Iyer", "Khan", "Gupta", "Singh", "Kumar",
        "Nair", "Das", "Mehta", "Joshi", "Verma", "Chowdary", "Menon", "Pillai", "Shah", "Bose"]
TESTS = [("718-7", "Hemoglobin", "g/dL", 13.5, 2.0), ("2345-7", "Glucose", "mg/dL", 110, 30),
         ("2160-0", "Creatinine", "mg/dL", 1.0, 0.4), ("2951-2", "Sodium", "mmol/L", 140, 3),
         ("6298-4", "Potassium", "mmol/L", 4.2, 0.5), ("4548-4", "Hemoglobin A1c", "%", 6.0, 1.2),
         ("2093-3", "Cholesterol", "mg/dL", 190, 35), ("789-8", "Erythrocytes", "10*12/L", 4.7, 0.5),
         ("6690-2", "Leukocytes", "10*9/L", 7.5, 2.5), ("777-3", "Platelets", "10*9/L", 250, 60)]

def patient(h, i):
    hn = int(h[1:])
    ident = i if i % 10 == 0 else hn * 10_000_000 + i  # every 10th patient is shared across hospitals
    g = h64(ident, 3) % 100
    return {"patient_id": f"P{i:07d}", "mrn": f"{h}-MRN{i:08d}",
            "first_name": FIRST[h64(ident, 1) % 20], "last_name": LAST[h64(ident, 2) % 20],
            "gender": "male" if g < 48 else "female" if g < 96 else "unknown",
            "birth_date": date(1930, 1, 1) + timedelta(days=h64(ident, 4) % 32000),
            "postal_code": str(500000 + h64(ident, 5) % 100000),
            "last_updated": t_of(h, "patient", i)}

def encounter(h, i):
    hn = int(h[1:]); t = t_of(h, "encounter", i)
    npat = max(1, int((t - START) / step(h, "patient")) + 1)
    r = h64(hn, i, 12) % 100
    cls = "IMP" if r < 20 else "AMB" if r < 85 else "EMER"
    if cls == "IMP": los = 86400 * (1 + h64(hn, i, 13) % 9) + h64(hn, i, 14) % 86400
    elif cls == "AMB": los = 1200 + h64(hn, i, 13) % 6000
    else: los = 3600 + h64(hn, i, 13) % 40000
    disch = t - 60; admit = disch - los
    if h64(hn, i, 15) % 100 == 0: disch = admit - 3600  # ~1% deliberately invalid rows
    return {"encounter_id": f"E{i:08d}", "patient_id": f"P{h64(hn, i, 11) % npat:07d}",
            "status": "finished", "class_code": cls, "admit_ts": admit, "discharge_ts": disch,
            "facility_id": f"F{1 + h64(hn, i, 16) % 5}", "last_updated": t}

def observation(h, i):
    t = t_of(h, "observation", i)
    e = int((t - START) / step(h, "encounter"))
    code, name, unit, mean, sd = TESTS[h64(int(h[1:]), i, 21) % len(TESTS)]
    u = h64(int(h[1:]), i, 22) / 2 ** 64
    return {"observation_id": f"O{i:09d}", "patient_id": encounter(h, e)["patient_id"],
            "encounter_id": f"E{e:08d}", "loinc_code": code, "test_name": name,
            "value_num": round(mean + sd * (u * 2 - 1) * 1.7, 2), "unit": unit,
            "effective_ts": t - 3600, "status": "final", "last_updated": t}

RECORD = {"patient": patient, "encounter": encounter, "observation": observation}

def iso(e): return datetime.fromtimestamp(e, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

def to_fhir(h, r, i):
    c = RECORD[r](h, i); meta = {"lastUpdated": iso(c["last_updated"])}
    if r == "patient":
        return {"resourceType": "Patient", "id": c["patient_id"], "meta": meta,
                "identifier": [{"system": f"urn:medilake:{h}:mrn", "value": c["mrn"]}],
                "name": [{"family": c["last_name"], "given": [c["first_name"]]}],
                "gender": c["gender"], "birthDate": c["birth_date"].isoformat(),
                "address": [{"postalCode": c["postal_code"]}]}
    if r == "encounter":
        return {"resourceType": "Encounter", "id": c["encounter_id"], "meta": meta, "status": c["status"],
                "class": {"system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": c["class_code"]},
                "subject": {"reference": f"Patient/{c['patient_id']}"},
                "period": {"start": iso(c["admit_ts"]), "end": iso(c["discharge_ts"])},
                "serviceProvider": {"reference": f"Organization/{c['facility_id']}"}}
    return {"resourceType": "Observation", "id": c["observation_id"], "meta": meta, "status": c["status"],
            "subject": {"reference": f"Patient/{c['patient_id']}"},
            "encounter": {"reference": f"Encounter/{c['encounter_id']}"},
            "code": {"coding": [{"system": "http://loinc.org", "code": c["loinc_code"], "display": c["test_name"]}]},
            "valueQuantity": {"value": c["value_num"], "unit": c["unit"]},
            "effectiveDateTime": iso(c["effective_ts"])}
