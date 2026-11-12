"""Manifest loading and validation for frozen data references.

A manifest JSON file describes the frozen data inputs for a backtest run:
file paths, SHA-256 hashes, source metadata, and coverage information.

Example manifest structure:
{
    "data_dir": "./frozen_data",
    "files": {
        "market_data": "market_data.csv",
        "spy_data": "spy_data.csv",
        "universe": "universe.json",
        "corporate_actions": "corporate_actions.json",
        "trading_calendar": "trading_calendar.json"
    },
    "checksums": {
        "market_data.csv": "sha256:abc123...",
        ...
    },
    "metadata": {
        "source": "CRSP / S&P DJI",
        "calendar_version": "2025-01",
        "data_version": "2025-11-10",
        "coverage_start": "1999-01-04",
        "coverage_end": "2025-12-31"
    }
}
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Optional


@dataclass
class DataManifest:
    """Parsed and validated data manifest."""

    data_dir: Path
    files: Dict[str, str]
    checksums: Dict[str, str]
    metadata: Dict[str, Any]

    def resolve(self, key: str) -> Path:
        """Resolve a data file path by key."""
        if key not in self.files:
            raise KeyError(f"Manifest missing file key: {key}")
        return self.data_dir / self.files[key]

    def verify_checksum(self, key: str) -> Optional[bool]:
        """Verify SHA-256 checksum of a data file.

        Returns True if checksum matches, False if mismatch,
        None if no checksum recorded for this key.
        """
        filename = self.files.get(key)
        if not filename:
            return None
        expected = self.checksums.get(filename)
        if not expected:
            return None

        path = self.data_dir / filename
        if not path.exists():
            return False

        actual = _sha256_file(path)
        expected_hash = expected.removeprefix("sha256:")
        return actual == expected_hash


def load_manifest(manifest_path: str) -> DataManifest:
    """Load and validate a manifest JSON file.

    Args:
        manifest_path: Path to manifest JSON

    Returns:
        Parsed DataManifest

    Raises:
        FileNotFoundError: If manifest or referenced data_dir missing
        ValueError: If manifest structure invalid
    """
    path = Path(manifest_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    with open(path, "r") as f:
        raw = json.load(f)

    if not isinstance(raw, dict):
        raise ValueError("Manifest must be a JSON object")

    # Resolve data_dir relative to manifest location
    data_dir_str = raw.get("data_dir", ".")
    data_dir = (path.parent / data_dir_str).resolve()

    files = raw.get("files", {})
    if not isinstance(files, dict):
        raise ValueError("Manifest 'files' must be a dict")

    required_keys = {"market_data", "spy_data", "universe", "trading_calendar"}
    missing = required_keys - set(files.keys())
    if missing:
        raise ValueError(f"Manifest missing required file keys: {sorted(missing)}")

    checksums = raw.get("checksums", {})
    metadata = raw.get("metadata", {})

    return DataManifest(
        data_dir=data_dir,
        files=files,
        checksums=checksums,
        metadata=metadata,
    )


def validate_manifest(manifest: DataManifest) -> Dict[str, Any]:
    """Validate all files referenced by the manifest.

    Returns a validation report dict.
    """
    report: Dict[str, Any] = {
        "status": "VALID",
        "files": {},
        "errors": [],
        "metadata": manifest.metadata,
    }

    for key, filename in manifest.files.items():
        file_path = manifest.data_dir / filename
        file_report: Dict[str, Any] = {"path": str(file_path)}

        if not file_path.exists():
            file_report["exists"] = False
            report["errors"].append(f"Missing file: {key} -> {filename}")
            report["status"] = "INVALID"
        else:
            file_report["exists"] = True
            file_report["size_bytes"] = file_path.stat().st_size

            checksum_result = manifest.verify_checksum(key)
            if checksum_result is True:
                file_report["checksum"] = "OK"
            elif checksum_result is False:
                file_report["checksum"] = "MISMATCH"
                report["errors"].append(f"Checksum mismatch: {key} -> {filename}")
                report["status"] = "INVALID"
            else:
                file_report["checksum"] = "NOT_RECORDED"

        report["files"][key] = file_report

    return report


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
