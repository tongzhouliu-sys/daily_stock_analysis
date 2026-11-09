"""Installed daily market-data collector; no credential values enter output."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.services.signal_scanner.core import DomainError, Settings
from src.services.signal_scanner.services.daily_market import DailyMarket
from src.services.signal_scanner.services.source_data import read_private_env


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    private = args.env_file or os.environ.get("SCANNER_ENV_FILE")
    if private:
        read_private_env(private)
    settings = Settings.load()
    if args.runtime:
        settings.runtime = args.runtime.resolve()
    collector = DailyMarket(settings)
    previous = None
    while True:
        try:
            result = collector.status() if args.status else collector.step(force=args.force)
        except Exception as exc:
            result = {
                "status": "ERROR",
                "code": exc.code if isinstance(exc, DomainError) else "COLLECTOR_INTERNAL_ERROR",
                "exception_type": type(exc).__name__,
            }
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
        if serialized != previous:
            print(serialized, flush=True)
            previous = serialized
        if not args.loop or args.status:
            return 1 if result["status"] == "ERROR" else 0
        time.sleep(3)


if __name__ == "__main__":
    raise SystemExit(main())
