"""Create the historical CSV.gz exports (hospital-specific columns, date formats, codes) plus
row_counts.csv, using the hist column names from your repo's config/mappings.json.
Usage: python scripts/generate_exports.py --mappings ../ehr-lakehouse/config/mappings.json --out exports
Then:  bash scripts/upload_historical.sh H1 exports/H1   (in the lakehouse repo)"""
import argparse, csv, gzip, json, os, sys
from datetime import datetime, timezone
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import gen

TS = {"H1": "%Y-%m-%d %H:%M:%S", "H2": "%d-%m-%Y %H:%M:%S", "H3": "%m/%d/%Y %H:%M"}
DT = {"H1": "%Y-%m-%d", "H2": "%d-%m-%Y", "H3": "%m/%d/%Y"}
GENDER = {"H1": dict(male="M", female="F", unknown="U"), "H2": dict(male="Male", female="Female", unknown="Unknown"),
          "H3": dict(male="M", female="F", unknown="U")}
CLASS = {"H1": dict(IMP="IP", AMB="OP", EMER="ER"), "H2": dict(IMP="Inpatient", AMB="Outpatient", EMER="Emergency"),
         "H3": dict(IMP="I", AMB="O", EMER="E")}
TSF = ("last_updated", "admit_ts", "discharge_ts", "effective_ts")


def fmt(h, r, k, v):
    if k in TSF: return datetime.fromtimestamp(v, timezone.utc).strftime(TS[h])
    if k == "birth_date": return v.strftime(DT[h])
    if k == "gender": return GENDER[h][v]
    if k == "class_code": return CLASS[h][v]
    if h == "H3" and k == "patient_id" and r != "patient": return "Patient/" + v
    if h == "H3" and k == "facility_id": return "Organization/" + v
    return v


def work(a):
    h, r, part, lo, hi, out, cols = a
    d = os.path.join(out, h, r); os.makedirs(d, exist_ok=True)
    f = gen.RECORD[r]
    with gzip.open(os.path.join(d, f"part-{part:05d}.csv.gz"), "wt", newline="", compresslevel=3) as fh:
        w = csv.writer(fh); w.writerow(cols.values())
        for i in range(lo, hi):
            c = f(h, i); w.writerow([fmt(h, r, k, c[k]) for k in cols])
    return h, r, hi - lo


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mappings", default="config/mappings.json")
    p.add_argument("--out", default="exports")
    p.add_argument("--rows-per-file", type=int, default=2_000_000)
    p.add_argument("--workers", type=int, default=os.cpu_count())
    p.add_argument("--hospitals", nargs="*", default=list(gen.HOSP))
    a = p.parse_args()
    maps = json.load(open(a.mappings))
    tasks = []
    for h in a.hospitals:
        for r in gen.RES:
            cols = {k: v for k, v in maps[r]["hist"][h].items() if v}
            n = gen.n_hist(h, r)
            for part, lo in enumerate(range(0, n, a.rows_per_file)):
                tasks.append((h, r, part, lo, min(n, lo + a.rows_per_file), a.out, cols))
    totals = {}
    with Pool(a.workers) as pool:
        for h, r, n in pool.imap_unordered(work, tasks):
            totals[(h, r)] = totals.get((h, r), 0) + n
            print(f"done {h}/{r}: +{n} rows", flush=True)
    t0 = datetime.fromtimestamp(gen.T0, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for h in a.hospitals:
        with open(os.path.join(a.out, h, "row_counts.csv"), "w") as fh:
            fh.write("hospital_id,table,source_rows,extracted_at\n")
            for r in gen.RES: fh.write(f"{h},{r},{totals[(h, r)]},{t0}\n")
    print("Exports ready in", a.out)
