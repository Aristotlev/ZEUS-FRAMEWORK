#!/usr/bin/env python3
"""
fal cost reconciliation — close the gap between estimates and actuals.

How it works:

  1. Reads ~/.hermes/zeus_fal_calls.jsonl (every paid fal call the pipeline made,
     with request_id + model + declared_cost + cost_source).
  2. Tries to pull actuals from fal's billing endpoints. Falls back to a manual
     summary if fal's billing API is locked behind dashboard-only auth.
  3. Writes a parallel reconciliation file at ~/.hermes/zeus_fal_reconciled.jsonl
     keyed by request_id. The pipeline ledger keeps its per-run rows untouched —
     this script's output lets you compute a delta (estimate vs. actual) without
     mutating the ledger.
  4. Prints a summary: total estimated spend, total reconciled-actual spend,
     remaining unreconciled call count, and per-model deltas.

Usage:
    python3 fal_reconcile.py
    python3 fal_reconcile.py --window-days 7
    python3 fal_reconcile.py --json

If you're seeing "could not reach fal billing" repeatedly: fal's REST billing
API (`/billing/usage`, `/me/usage`) varies by org tier. The side log
(zeus_fal_calls.jsonl) is enough to manually reconcile against the fal dashboard
at https://fal.ai/dashboard/billing.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from datetime import datetime, timedelta
from typing import Optional

import requests

FAL_CALL_LOG = pathlib.Path(os.path.expanduser("~/.hermes/zeus_fal_calls.jsonl"))
RECONCILED_LOG = pathlib.Path(os.path.expanduser("~/.hermes/zeus_fal_reconciled.jsonl"))

# fal exposes billing at a few possible endpoints depending on org. Try each;
# whichever works wins. None is guaranteed to be reachable with FAL_KEY alone.
FAL_BILLING_ENDPOINTS = [
    "https://rest.alpha.fal.ai/billing/usage",
    "https://api.fal.ai/billing/usage",
    "https://fal.run/billing/usage",
]


def _read_calls(window_days: Optional[int]) -> list[dict]:
    if not FAL_CALL_LOG.exists():
        return []
    cutoff = datetime.utcnow() - timedelta(days=window_days) if window_days else None
    out = []
    with FAL_CALL_LOG.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cutoff is not None:
                try:
                    ts = datetime.fromisoformat(row.get("ts", ""))
                except ValueError:
                    continue
                if ts < cutoff:
                    continue
            out.append(row)
    return out


def _read_reconciled() -> dict[str, dict]:
    """{request_id: latest reconciled row}"""
    if not RECONCILED_LOG.exists():
        return {}
    out: dict[str, dict] = {}
    with RECONCILED_LOG.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = row.get("request_id")
            if rid:
                out[rid] = row
    return out


def _try_fetch_fal_billing() -> Optional[list[dict]]:
    """
    Try each known fal billing endpoint with FAL_KEY. Returns a list of
    {request_id, cost_usd, ts, model} rows on success, None if every endpoint
    rejects us. Caller falls back to manual reconciliation if None.
    """
    fal_key = os.getenv("FAL_KEY")
    if not fal_key:
        print("WARN: FAL_KEY not set — skipping API call. Use the side-log summary below.")
        return None
    headers = {"Authorization": f"Key {fal_key}", "Accept": "application/json"}
    for url in FAL_BILLING_ENDPOINTS:
        try:
            r = requests.get(url, headers=headers, timeout=10)
        except requests.RequestException:
            continue
        if r.status_code == 200:
            try:
                payload = r.json()
            except json.JSONDecodeError:
                continue
            # Try a few shapes: {usage: [...]}, {data: [...]}, [...]
            rows = payload.get("usage") if isinstance(payload, dict) else None
            if rows is None and isinstance(payload, dict):
                rows = payload.get("data")
            if rows is None and isinstance(payload, list):
                rows = payload
            if isinstance(rows, list):
                norm: list[dict] = []
                for r_ in rows:
                    if not isinstance(r_, dict):
                        continue
                    norm.append({
                        "request_id": r_.get("request_id") or r_.get("id"),
                        "cost_usd": r_.get("cost") or r_.get("amount") or r_.get("cost_usd"),
                        "ts": r_.get("ts") or r_.get("created_at"),
                        "model": r_.get("model") or r_.get("endpoint"),
                    })
                print(f"  fal billing reached: {url} ({len(norm)} usage rows)")
                return norm
    return None


def _write_reconciled(rows: list[dict]) -> None:
    if not rows:
        return
    RECONCILED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with RECONCILED_LOG.open("a") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Reconcile pipeline fal estimates against fal billing")
    ap.add_argument("--window-days", type=int, default=30, help="Reconcile calls from last N days")
    ap.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = ap.parse_args()

    calls = _read_calls(args.window_days)
    if not calls:
        print(f"No fal calls in last {args.window_days}d (log: {FAL_CALL_LOG})")
        return 0

    by_id: dict[str, dict] = {c["request_id"]: c for c in calls if c.get("request_id")}
    estimated_total = sum(float(c.get("declared_cost_usd") or 0) for c in calls)
    by_model_est: dict[str, float] = {}
    for c in calls:
        by_model_est[c["model"]] = by_model_est.get(c["model"], 0.0) + float(c.get("declared_cost_usd") or 0)

    print("=" * 70)
    print(f"  fal reconciliation — last {args.window_days}d")
    print("=" * 70)
    print(f"  {len(calls)} fal calls in side-log")
    print(f"  estimated spend: ${estimated_total:.4f}")
    for m, v in sorted(by_model_est.items(), key=lambda x: -x[1]):
        print(f"    {m}: ${v:.4f}")
    print()

    print("Attempting to fetch fal billing actuals...")
    billing = _try_fetch_fal_billing()
    if billing is None:
        print()
        print("⚠ Could not reach fal billing API (likely dashboard-only auth).")
        print(f"   Manually reconcile the side log (which has request_ids) at:")
        print(f"   https://fal.ai/dashboard/billing")
        print(f"   Side log: {FAL_CALL_LOG}")
        if args.json:
            json.dump(
                {
                    "calls": len(calls),
                    "estimated_total_usd": round(estimated_total, 4),
                    "actual_total_usd": None,
                    "by_model_estimated": by_model_est,
                    "reconciled_count": 0,
                    "billing_api_reached": False,
                },
                sys.stdout,
                indent=2,
            )
            print()
        return 0

    matched = 0
    actual_total = 0.0
    by_model_actual: dict[str, float] = {}
    reconciled_rows: list[dict] = []
    for b in billing:
        rid = b.get("request_id")
        if not rid:
            continue
        original = by_id.get(rid)
        if not original:
            continue
        cost = float(b.get("cost_usd") or 0)
        actual_total += cost
        m = original.get("model") or b.get("model") or "unknown"
        by_model_actual[m] = by_model_actual.get(m, 0.0) + cost
        matched += 1
        reconciled_rows.append({
            "ts": datetime.utcnow().isoformat(),
            "request_id": rid,
            "run_id": original.get("run_id"),
            "model": m,
            "estimated_cost_usd": float(original.get("declared_cost_usd") or 0),
            "actual_cost_usd": cost,
            "delta_usd": round(cost - float(original.get("declared_cost_usd") or 0), 6),
        })

    _write_reconciled(reconciled_rows)

    print()
    print(f"  matched {matched} of {len(calls)} calls against fal billing")
    print(f"  actual spend (matched only): ${actual_total:.4f}")
    print(f"  delta vs. estimate:           ${actual_total - estimated_total:+.4f}")
    print()
    for m, v in sorted(by_model_actual.items(), key=lambda x: -x[1]):
        est = by_model_est.get(m, 0.0)
        delta = v - est
        marker = "✓" if abs(delta) < 0.01 else ("↑" if delta > 0 else "↓")
        print(f"    {m}: actual ${v:.4f} vs est ${est:.4f}  {marker} delta ${delta:+.4f}")
    print()
    print(f"  Reconciled rows written to {RECONCILED_LOG}")

    if args.json:
        json.dump(
            {
                "calls": len(calls),
                "matched": matched,
                "estimated_total_usd": round(estimated_total, 4),
                "actual_total_usd": round(actual_total, 4),
                "by_model_estimated": by_model_est,
                "by_model_actual": by_model_actual,
                "reconciled_log": str(RECONCILED_LOG),
                "billing_api_reached": True,
            },
            sys.stdout,
            indent=2,
        )
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
