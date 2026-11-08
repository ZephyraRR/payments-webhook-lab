"""Reconciliation report.

Compares what the webhooks recorded with a provider export (CSV with provider, payment_id, amount, currency, status)
and lists every mismatch. This is the part a support or finance person actually needs.
"""
import csv
import sys
from sqlite3 import Connection


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
        elif int(row["amount"]) != mine["amount"]:
            issues.append({"payment_id": row["payment_id"], "problem": f"amount differs: export {row['amount']} vs webhooks {mine['amount']}"})
        elif row["status"] != mine["status"]:
            issues.append({"payment_id": row["payment_id"], "problem": f"status differs: export {row['status']} vs webhooks {mine['status']}"})
    for key, mine in known.items():
        if key not in seen:
            issues.append({"payment_id": key[1], "problem": "webhook recorded but missing in provider export"})
    return issues


def main(db_path: str, csv_path: str) -> int:
    from .db import connect

    con = connect(db_path)
    with open(csv_path, newline="", encoding="utf-8") as f:
        issues = reconcile(con, list(csv.DictReader(f)))
    for i in issues:
        print(f"{i['payment_id']}: {i['problem']}")
    print(f"{len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
