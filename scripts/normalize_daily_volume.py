#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R26: dry-run or apply the daily volume unit migration (lots -> shares).

Usage:
    python scripts/normalize_daily_volume.py             # dry run
    python scripts/normalize_daily_volume.py --apply     # backup + migrate
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config import get_config
from src.storage import migrate_daily_volume_to_shares


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the migration; without this flag only a dry run is performed.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the database URL (default: config.get_db_url()).",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the SQLite file backup when applying.",
    )
    args = parser.parse_args()

    db_url = args.database_url or get_config().get_db_url()
    engine = create_engine(db_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    stats = migrate_daily_volume_to_shares(
        engine,
        session_factory,
        dry_run=not args.apply,
        backup=not args.no_backup,
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
