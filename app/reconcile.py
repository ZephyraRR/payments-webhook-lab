"""Reconciliation report.

Compares what the webhooks recorded with a provider export (CSV: provider, payment_id, amount, currency, status) and lists every mismatch,
plus things the webhooks themselves flagged (refunds or disputes that never found their payment, refunds larger than the payment).
This is the part a support or finance person actually needs.
"""
import csv
import sys
from sqlite3 import Connection

REQUIRED = ("provider", "payment_id", "amount", "currency", "status")


def reconcile(con: Connection, export_rows: list[dict]) -> list[dict]:
    issues = []
    known = {(r["provider"], r["payment_id"]): r for r in con.execute("SELECT * FROM payments")}
    seen = set()
    for row in export_rows:
        key = (row["provider"], row["payment_id"])
        seen.add(key)
        mine = known.get(key)
        if mine is None:
            issues.append({"payment_id": row["payment_id"], "problem": "in provider export but no webhook received"})
            continue
        try:
            export_amount = int(row["amount"])
        except ValueError:
            issues.append({"payment_id": row["payment_id"], "problem": f"export amount is not an integer: {row['amount']!r}"})
            continue
        if export_amount != mine["amount"]:
            issues.append({"payment_id": row["payment_id"], "problem": f"amount differs: export {export_amount} vs webhooks {mine['amount']}"})
        elif row["currency"].upper() != mine["currency"]:
            issues.append({"payment_id": row["payment_id"], "problem": f"currency differs: export {row['currency'].upper()} vs webhooks {mine['currency']}"})
        elif row["status"] != mine["status"]:
            issues.append({"payment_id": row["payment_id"], "problem": f"status differs: export {row['status']} vs webhooks {mine['status']}"})
    for key, mine in known.items():
        if key not in seen:
            issues.append({"payment_id": key[1], "problem": "webhook recorded but missing in provider export"})
    for p in con.execute("SELECT * FROM pending"):
        issues.append({"payment_id": p["payment_id"], "problem": f"{p['kind']} event {p['event_id']} arrived but the payment webhook never did"})
    for a in con.execute("SELECT * FROM anomalies"):
        issues.append({"payment_id": a["payment_id"], "problem": a["note"]})
    return issues


def main(db_path: str, csv_path: str) -> int:
    from .db import connect

    con = connect(db_path)
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
        if missing:
            print(f"export is missing columns: {', '.join(missing)}")
            return 2
        issues = reconcile(con, list(reader))
    for i in issues:
        print(f"{i['payment_id']}: {i['problem']}")
    print(f"{len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
