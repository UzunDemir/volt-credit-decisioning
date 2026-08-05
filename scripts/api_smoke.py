"""API smoke test — health, model-info, live scoring.

Usage:
    python scripts/api_smoke.py [--app-id 1]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

API = "http://localhost:8000"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-id", type=int, default=1)
    args = ap.parse_args()

    health = httpx.get(f"{API}/health", timeout=5)
    print("health:", health.status_code, health.json())

    info = httpx.get(f"{API}/model-info", timeout=5)
    print("model-info:", info.status_code, info.json())

    r = httpx.post(f"{API}/v1/score", json={"application_id": args.app_id}, timeout=30)
    print("score:", r.status_code, r.text[:500])


if __name__ == "__main__":
    main()
