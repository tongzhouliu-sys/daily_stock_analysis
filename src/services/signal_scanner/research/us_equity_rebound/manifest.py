"""Data manifest validation and loading."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from src.services.signal_scanner.core import DomainError, require


def validate_manifest(manifest_path: str) -> Dict[str, Any]:
    """Validate data manifest and return manifest dict.
    
    Checks:
    - File exists and is valid JSON
    - Required fields present
    - SHA-256 hashes match referenced files
    - Data files exist
    """
    path = Path(manifest_path).resolve()
    require(path.is_file(), "MANIFEST_NOT_FOUND", status=404)
    
    try:
        manifest = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise DomainError("INVALID_MANIFEST", str(e), status=422) from e
    
    # Required fields
    required = [
        "schema_version",
        "data_kind",
        "calendar_version",
        "universe_version",
        "start_date",
        "end_date",
        "files",
    ]
    
    missing = [f for f in required if f not in manifest]
    require(not missing, "MISSING_MANIFEST_FIELDS", status=422, fields=missing)
    
    require(manifest["schema_version"] == 1, "UNSUPPORTED_SCHEMA_VERSION", status=422)
    
    # Validate file references
    manifest_dir = path.parent
    files = manifest["files"]
    
    require(isinstance(files, dict), "INVALID_FILES_FIELD", status=422)
    
    for key, file_ref in files.items():
        require(isinstance(file_ref, dict), "INVALID_FILE_REF", status=422, field=key)
        require("path" in file_ref, "MISSING_FILE_PATH", status=422, field=key)
        require("sha256" in file_ref, "MISSING_FILE_HASH", status=422, field=key)
        
        file_path = (manifest_dir / file_ref["path"]).resolve()
        require(file_path.is_file(), "REFERENCED_FILE_NOT_FOUND", status=422, field=key)
        
        # Verify hash
        content = file_path.read_bytes()
        actual_hash = hashlib.sha256(content).hexdigest()
        require(
            actual_hash == file_ref["sha256"],
            "FILE_HASH_MISMATCH",
            status=422,
            field=key,
            expected=file_ref["sha256"],
            actual=actual_hash,
        )
    
    return manifest


def load_data(manifest: Dict[str, Any], manifest_path: str) -> Dict[str, Any]:
    """Load all data files referenced in manifest.
    
    Returns dict with loaded data keyed by file type.
    """
    path = Path(manifest_path).resolve()
    manifest_dir = path.parent
    data = {}
    
    for key, file_ref in manifest["files"].items():
        file_path = (manifest_dir / file_ref["path"]).resolve()
        content = file_path.read_bytes()
        
        # Parse based on file type
        if file_path.suffix == ".json":
            data[key] = json.loads(content)
        elif file_path.suffix == ".csv":
            # CSV files loaded as-is for now
            data[key] = content.decode("utf-8")
        else:
            data[key] = content
    
    return data
