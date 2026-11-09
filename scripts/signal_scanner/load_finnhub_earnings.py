"""One-off small-pilot earnings calendar import. Missing coverage never means no event."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.services.signal_scanner.core import Settings
from src.services.signal_scanner.services.source_data import load_finnhub, read_private_env

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--symbols", default="AAPL,MSFT,NVDA")
    args = parser.parse_args()
    if args.env_file:
        read_private_env(args.env_file)
    settings = Settings.load()
    if args.runtime:
        settings.runtime = args.runtime.resolve()
    result = load_finnhub(settings, symbols=[s.strip().upper() for s in args.symbols.split(",") if s.strip()])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "SUCCEEDED" else 1)
