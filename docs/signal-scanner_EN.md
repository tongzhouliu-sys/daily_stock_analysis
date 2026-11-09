# Integrated signal scanner

Open **Signal scanner** in the DSA sidebar (`/scanner`). The imported scanner uses DSA authentication and the existing application shell. Its API is mounted at `/api/v1/scanner`.

The integration includes rules and features, signal lifecycle, evidence and data-quality gates, manual STOP review, plans and reservations, paper accounts and ledger, journal, backups/restore, exports, batch history and isolated rule validation. Scanner accounts and raw rule events remain separate from DSA portfolio balances and AI decision signals.

The signal detail page loads the ticker and a pinned signal revision into a DSA Chat draft, including triggers, frozen levels, scores, missing values and counter-evidence. Stock/revision mismatches are rejected; long evidence is explicitly truncated. User edits are preserved and analysis is never submitted automatically.

Install the updated requirements, build the Web client, and start DSA normally. Managed workers are controlled by `SCANNER_WORKERS_ENABLED` (default `true`). `SCANNER_RUNTIME` defaults to `signal-scanner/` beside `DATABASE_PATH`. `SCANNER_DAILY_MARKET` defaults to `false`; enabling it also requires configured data providers, a universe and valid credentials.

Set `SCANNER_ORIGIN` to the public site origin when needed by the reverse proxy. Optional `SCANNER_LEGACY_HOST` redirects old scanner pages to that origin; old-site mutations are rejected, never replayed. Writes require an allowed Origin and a session-bound CSRF token. Data-source credentials and research-directory options are listed in `.env.example` and the [Chinese configuration reference](signal-scanner.md).

Lifecycle, directory and private provider settings are explicitly excluded from the generic Web settings editor and require a restart. Versioned scanner domain settings remain available in its own Settings page. Before creating a research experiment, configure `RULE_VALIDATION_INPUT_ROOTS` and register its manifest through `python -m src.services.signal_scanner.worker.rule_validation --root /path/to/control --input-root /path/to/inputs register-dataset /path/to/inputs/sample/manifest.json --name sample`. The control root must match the Web root.

Data migration is explicit: stop source writers, then run `python scripts/signal_scanner/migrate_runtime.py --source /path/to/old/runtime --destination /path/to/new/runtime`. The tool verifies SQLite integrity and file hashes, refuses existing destinations, preserves business records, and excludes old credentials, sessions, locks and backup archives. Keep the original runtime for rollback. Missing data remains visibly unknown. Research scores are not probabilities, and EOD replay does not establish RTH/PIT validity or permission to trade.

The standalone CLI is available as `python -m src.services.signal_scanner.cli`; offline research uses `python -m src.services.signal_scanner.research.cli`. Historical host-specific installers are archived as reference text under `docs/signal-scanner/upstream-tools/` and are not part of the active deployment path.

Run the imported regression suite and host integration tests, followed by the repository backend gate and Web lint/build. Disable managed workers and restart to stop background execution. A code rollback must retain the scanner runtime and research artifacts and must not overwrite unrelated local changes.

Release validation covers the full backend gate, Web tests/lint/build, real worker recovery, migration integrity and production login/navigation. Managed workers restart after unexpected exits; valid DSA sessions and scanner CSRF tokens survive a service restart. Logout invalidates the shared session across modules.

Before cutover, back up DSA configuration/database and both proxy configurations. Point the old hostname at DSA only after the migrated runtime is ready. Disable old service autostart to prevent duplicate collectors. To roll back, stop new workers, restore prior private/proxy configuration and restart the preserved source services. Retain all newly generated data; never overwrite it with the old copy. See the Chinese reference for the complete procedure and release evidence. Historical `app.*` research snapshots retain their original hashes; do not rewrite them to impersonate the integrated implementation.
