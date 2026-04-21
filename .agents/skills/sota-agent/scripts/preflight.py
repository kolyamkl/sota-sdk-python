#!/usr/bin/env python3
"""Pre-deploy sanity check for a SOTA agent.

Validates that the env vars are set, the backend is reachable, the API
key works, and prints the agent's current lifecycle status. Run BEFORE
`python agent.py` (or `npm start`) to catch misconfigurations before
they look like "the agent isn't receiving jobs".

Usage:
    python3 preflight.py
    python3 preflight.py --api-key sk_...        # override env
    python3 preflight.py --api-url http://...    # override env

Exit codes:
    0 — all checks passed, agent should be able to start
    1 — configuration or connectivity problem; details printed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


DEFAULT_API_URL = "http://localhost:3001"
REQUIRED_ENV = ["SOTA_API_KEY"]
RECOMMENDED_ENV = ["SOTA_API_URL", "SUPABASE_URL", "SUPABASE_ANON_KEY"]


def _check(label: str, ok: bool, detail: str = "") -> None:
    mark = "✓" if ok else "✗"
    line = f"  [{mark}] {label}"
    if detail:
        line += f"  — {detail}"
    print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-key", default=os.environ.get("SOTA_API_KEY", ""))
    parser.add_argument("--api-url", default=os.environ.get("SOTA_API_URL", DEFAULT_API_URL))
    args = parser.parse_args()

    failures = 0

    # --- 1. Required env vars ---
    print("Environment variables:")
    if not args.api_key:
        _check("SOTA_API_KEY is set", False, "not set — agent can't authenticate")
        failures += 1
    else:
        _check("SOTA_API_KEY is set", True, f"length={len(args.api_key)}")

    # --- 2. Recommended env vars ---
    for var in RECOMMENDED_ENV:
        val = os.environ.get(var)
        if not val:
            _check(f"{var}", False, "not set — Realtime will be disabled, poll-only fallback")
        else:
            _check(f"{var}", True, val if len(val) < 80 else val[:80] + "…")

    print()

    if failures:
        print("Aborting further checks — fix required env vars first.")
        return 1

    # --- 3. Backend reachability ---
    print(f"Backend at {args.api_url}:")
    health_url = f"{args.api_url.rstrip('/')}/health"
    try:
        with urllib.request.urlopen(health_url, timeout=5) as resp:
            _check("health endpoint reachable", resp.status == 200, f"HTTP {resp.status}")
    except urllib.error.URLError as e:
        _check("health endpoint reachable", False, str(e))
        failures += 1

    # --- 4. Developer config ---
    cfg_url = f"{args.api_url.rstrip('/')}/api/v1/developer/config"
    cfg = None
    try:
        with urllib.request.urlopen(cfg_url, timeout=5) as resp:
            if resp.status == 200:
                cfg = json.loads(resp.read().decode())
                _check("developer config reachable", True, f"supabase_url={cfg.get('supabase_url','?')}")
            else:
                _check("developer config reachable", False, f"HTTP {resp.status}")
    except urllib.error.URLError as e:
        _check("developer config reachable", False, str(e))

    print()

    # --- 5. Agent auth + status ---
    print("Agent authentication:")
    me_url = f"{args.api_url.rstrip('/')}/api/v1/agents/me"
    req = urllib.request.Request(me_url, headers={"X-API-Key": args.api_key})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                me = json.loads(resp.read().decode())
                _check("API key accepted", True, f"agent={me.get('name','?')} id={me.get('id','?')}")
                print()
                print("Current agent state:")
                print(f"  status:        {me.get('status','?')}")
                print(f"  capabilities:  {me.get('capabilities', [])}")
                print(f"  last_seen_at:  {me.get('last_seen_at','never')}")
                print(f"  sdk_version:   {me.get('sdk_version','none')}")

                status = me.get("status", "")
                print()
                if status == "sandbox":
                    print("  → Sandbox mode: start the agent and it will poll for 3 test jobs.")
                elif status == "testing_passed":
                    print("  → All sandbox tests passed. Run `sota-agent request-review` next.")
                elif status == "pending_review":
                    print("  → Waiting on admin review. Agent can idle-run safely.")
                elif status == "active":
                    print("  → Active. Agent should receive real marketplace jobs via Realtime.")
                elif status == "rejected":
                    print("  → Rejected by admin. Fix issues, then register a NEW agent.")
                elif status == "suspended":
                    print("  → Suspended by admin. Contact support.")
                else:
                    print(f"  → Unknown status '{status}'. Check the DevPortal.")
            else:
                _check("API key accepted", False, f"HTTP {resp.status}")
                failures += 1
    except urllib.error.HTTPError as e:
        if e.code == 401:
            _check("API key accepted", False, "HTTP 401 — key revoked or wrong")
        else:
            _check("API key accepted", False, f"HTTP {e.code}: {e.reason}")
        failures += 1
    except urllib.error.URLError as e:
        _check("API key accepted", False, str(e))
        failures += 1

    print()
    if failures:
        print(f"FAIL: {failures} check(s) failed. Fix before running the agent.")
        return 1
    print("OK: environment is ready. Start the agent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
