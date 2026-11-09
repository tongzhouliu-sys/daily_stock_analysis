import hashlib
import json
import os
from pathlib import Path

from src.services.signal_scanner.core import canonical, require


class Artifacts:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def save(self, value, kind="snapshots"):
        content = canonical(value).encode()
        h = hashlib.sha256(content).hexdigest()
        folder = self.root / kind
        folder.mkdir(exist_ok=True, mode=0o700)
        path = folder / f"{h}.json"
        if path.exists():
            require(hashlib.sha256(path.read_bytes()).hexdigest() == h, "ARTIFACT_CORRUPT", status=503)
        else:
            temporary = folder / f".{h}.{os.getpid()}.tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        return f"{kind}/{h}.json"

    def read(self, ref):
        require(isinstance(ref, str), "MISSING_REFERENCED_INPUT", status=503)
        path = (self.root / ref).resolve()
        require(path.is_relative_to(self.root) and path.is_file(), "MISSING_REFERENCED_INPUT", status=503)
        content = path.read_bytes()
        require(hashlib.sha256(content).hexdigest() == path.stem, "ARTIFACT_CORRUPT", status=503)
        return json.loads(content)
