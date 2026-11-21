"""Configuration file manager with atomic read/write behavior."""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Literal, Optional, Set, Tuple

from dotenv import dotenv_values

_ASSIGNMENT_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
_FALLBACK_REWRITE_ERRNOS = {errno.EBUSY, errno.EXDEV}
_COMPOSE_ESCAPED_ENV_VALUE_KEYS = frozenset({"CUSTOM_WEBHOOK_BODY_TEMPLATE"})
_APPLICATION_TEMPLATE_PLACEHOLDER_PATTERN = re.compile(
    r"(?<!\$)\$(?:"
    r"\{(content_json|title_json|content|title)\}"
    r"|(content_json|title_json|content|title)\b"
    r")"
)
_ESCAPED_APPLICATION_TEMPLATE_PLACEHOLDER_PATTERN = re.compile(
    r"\$\$(?:"
    r"\{(content_json|title_json|content|title)\}"
    r"|(content_json|title_json|content|title)\b"
    r")"
)

logger = logging.getLogger(__name__)

# R2: characters that must never appear inside a stored ``.env`` value.  They
# are all treated as line breaks (or dropped) by ``dotenv``/``splitlines()``,
# so a value carrying one could be re-read as a second assignment.
_UNSAFE_ENV_VALUE_CHAR_PATTERN = re.compile(
    "[\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]"
)


def _split_env_lines(text: str) -> List[str]:
    """Split ``.env`` text on ``\\n`` only, tolerating ``\\r\\n`` endings.

    ``str.splitlines()`` also splits on \\x0b, \\x0c, \\x1c-\\x1e, \\x85,
    \\u2028 and \\u2029, which would silently break a value that contains one
    of them into a separate line.  Splitting on ``\\n`` keeps the file layout
    stable and lets :func:`warn_unsafe_env_lines` report such values instead.
    """
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def find_unsafe_env_lines(text: str) -> List[Tuple[int, str]]:
    """Return ``(line_number, offending_char)`` for ``.env`` lines at risk."""
    hits: List[Tuple[int, str]] = []
    for line_number, raw_line in enumerate(_split_env_lines(text), start=1):
        match = _UNSAFE_ENV_VALUE_CHAR_PATTERN.search(raw_line)
        if match is not None:
            hits.append((line_number, match.group(0)))
    return hits


def warn_unsafe_env_lines(env_path: Path) -> None:
    """Log a warning for every ``.env`` line carrying a line-separator char."""
    try:
        if not env_path.exists():
            return
        text = env_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - defensive branch
        logger.debug("Skipping .env safety scan for %s: %s", env_path, exc)
        return
    for line_number, char in find_unsafe_env_lines(text):
        logger.warning(
            "Unsafe control character U+%04X found in %s line %d; "
            "rewrite this value through the settings API or by hand.",
            ord(char),
            env_path,
            line_number,
        )


def _assert_single_line_env_value(key: str, value: str) -> None:
    """Refuse to mutate/write a value that would break the ``.env`` layout.

    Unlike the previous behaviour this never rewrites the caller's value:
    silently stripping characters let a crafted payload pass validation and
    still land in the file.
    """
    if "\n" in value or "\r" in value or _UNSAFE_ENV_VALUE_CHAR_PATTERN.search(value):
        raise ValueError(
            f"Refusing to write {key}: value contains a line break or control character"
        )



def escape_compose_sensitive_env_value(key: str, value: str) -> str:
    """Escape app template placeholders that Docker Compose would interpolate."""
    if key.upper() not in _COMPOSE_ESCAPED_ENV_VALUE_KEYS:
        return value

    def _replace(match: re.Match[str]) -> str:
        braced_name = match.group(1)
        plain_name = match.group(2)
        if braced_name is not None:
            return f"$${{{braced_name}}}"
        return f"$${plain_name}"

    return _APPLICATION_TEMPLATE_PLACEHOLDER_PATTERN.sub(_replace, value)


def unescape_compose_sensitive_env_value(key: str, value: str) -> str:
    """Restore app template placeholders escaped for Docker Compose storage."""
    if key.upper() not in _COMPOSE_ESCAPED_ENV_VALUE_KEYS:
        return value

    def _replace(match: re.Match[str]) -> str:
        braced_name = match.group(1)
        plain_name = match.group(2)
        if braced_name is not None:
            return f"${{{braced_name}}}"
        return f"${plain_name}"

    return _ESCAPED_APPLICATION_TEMPLATE_PLACEHOLDER_PATTERN.sub(_replace, value)


@dataclass
class ConfigLineEntry:
    """Structured representation of a single `.env` line."""

    kind: Literal["assignment", "comment", "blank", "raw"]
    raw_line: str
    key: Optional[str] = None
    value: str = ""
    updated: bool = False

    @classmethod
    def parse(cls, raw_line: str) -> "ConfigLineEntry":
        stripped = raw_line.strip()
        if not stripped:
            return cls(kind="blank", raw_line=raw_line)
        if stripped.startswith("#"):
            return cls(kind="comment", raw_line=raw_line)

        matched = _ASSIGNMENT_PATTERN.match(raw_line)
        if matched:
            return cls(
                kind="assignment",
                raw_line=raw_line,
                key=matched.group(1),
                value=matched.group(2),
            )

        return cls(kind="raw", raw_line=raw_line)

    @classmethod
    def assignment(cls, key: str, value: str) -> "ConfigLineEntry":
        return cls(
            kind="assignment",
            raw_line=f"{key}={value}",
            key=key,
            value=value,
            updated=True,
        )

    def render(self) -> str:
        if self.kind == "assignment" and self.updated and self.key is not None:
            return f"{self.key}={self.value}"
        return self.raw_line


class ConfigManager:
    """Manage `.env` read/write operations with optimistic versioning."""

    def __init__(self, env_path: Optional[Path] = None):
        self._env_path = env_path or self._resolve_env_path()
        self._lock = threading.RLock()

    @property
    def env_path(self) -> Path:
        """Return active `.env` path."""
        return self._env_path

    def read_config_map(self) -> Dict[str, str]:
        """Read key-value mapping from `.env` file."""
        return self._read_config_map(normalize_values=True)

    def _read_config_map(self, *, normalize_values: bool) -> Dict[str, str]:
        """Read key-value mapping from `.env` file."""
        if not self._env_path.exists():
            return {}

        raw_values = dotenv_values(self._env_path, interpolate=False)
        if normalize_values:
            # Display keeps dotenv interpolation so the settings page matches the
            # runtime value. Writes through the API reject ``${OTHER_KEY}``
            # references (see SystemConfigService._env_value_unsafe_reason), so a
            # caller cannot plant one to copy a secret into a displayed field.
            values = dotenv_values(self._env_path)
            for raw_key, raw_value in raw_values.items():
                if (
                    raw_key is not None
                    and str(raw_key).upper() in _COMPOSE_ESCAPED_ENV_VALUE_KEYS
                ):
                    values[raw_key] = raw_value
        else:
            values = raw_values
        config_map: Dict[str, str] = {}
        for key, value in values.items():
            if key is None:
                continue
            normalized_key = str(key)
            normalized_value = "" if value is None else str(value)
            if normalize_values:
                normalized_value = unescape_compose_sensitive_env_value(
                    normalized_key,
                    normalized_value,
                )
            config_map[normalized_key] = normalized_value
        return config_map

    def get_config_version(self) -> str:
        """Return deterministic version string based on file state."""
        if not self._env_path.exists():
            return "missing:0"

        content = self._env_path.read_bytes()
        file_stat = self._env_path.stat()
        content_hash = hashlib.sha256(content).hexdigest()
        return f"{file_stat.st_mtime_ns}:{content_hash}"

    def get_updated_at(self) -> Optional[str]:
        """Return `.env` last update time in ISO8601 format."""
        if not self._env_path.exists():
            return None

        file_stat = self._env_path.stat()
        updated_at = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)
        return updated_at.isoformat()

    def apply_updates(
        self,
        updates: Iterable[Tuple[str, str]],
        sensitive_keys: Set[str],
        mask_token: str,
    ) -> Tuple[List[str], List[str], str]:
        """Apply updates into `.env` file using atomic replace when possible."""
        with self._lock:
            current_values = self.read_config_map()
            stored_values = self._read_config_map(normalize_values=False)
            mutable_updates: Dict[str, str] = {}
            skipped_masked: List[str] = []

            for key, value in updates:
                key_upper = key.upper()
                current_value = current_values.get(key_upper)

                if key_upper in sensitive_keys and value == mask_token:
                    if current_value not in (None, ""):
                        skipped_masked.append(key_upper)
                    continue

                _assert_single_line_env_value(key_upper, value)
                stored_value = stored_values.get(key_upper)
                canonical_stored_value = escape_compose_sensitive_env_value(
                    key_upper,
                    value,
                )
                if current_value == value and (
                    key_upper not in _COMPOSE_ESCAPED_ENV_VALUE_KEYS
                    or stored_value == canonical_stored_value
                ):
                    continue

                mutable_updates[key_upper] = value

            if mutable_updates:
                self._atomic_upsert(mutable_updates)

            return list(mutable_updates.keys()), skipped_masked, self.get_config_version()

    def _atomic_upsert(self, updates: Dict[str, str]) -> None:
        """Write updates with atomic rename and in-place fallback for mounted files."""
        entries = self._read_entries()
        key_to_index = self._find_last_key_indexes(entries)

        for key, value in updates.items():
            _assert_single_line_env_value(key, value)
            line_value = escape_compose_sensitive_env_value(key, value)
            if key in key_to_index:
                entries[key_to_index[key]] = ConfigLineEntry.assignment(key, line_value)
            else:
                entries.append(ConfigLineEntry.assignment(key, line_value))

        if not self._env_path.parent.exists():
            self._env_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = self._env_path.with_suffix(self._env_path.suffix + ".tmp")
        content = "\n".join(entry.render() for entry in entries)
        if content and not content.endswith("\n"):
            content += "\n"

        with temp_path.open("w", encoding="utf-8", newline="\n") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())

        # .env carries API keys and webhook secrets; do not rely on umask.
        try:
            os.chmod(temp_path, 0o600)
        except OSError:
            logger.debug("chmod 0600 on temporary .env failed", exc_info=True)

        try:
            os.replace(temp_path, self._env_path)
        except OSError as exc:
            if exc.errno not in _FALLBACK_REWRITE_ERRNOS:
                raise

            logger.warning(
                "Atomic replace for .env failed with errno=%s, falling back to in-place rewrite",
                exc.errno,
            )
            self._rewrite_in_place(content)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _rewrite_in_place(self, content: str) -> None:
        """Rewrite `.env` content in place when rename is unsupported by mount type."""
        with self._env_path.open("w", encoding="utf-8", newline="\n") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        try:
            os.chmod(self._env_path, 0o600)
        except OSError:
            logger.debug("chmod 0600 on in-place .env failed", exc_info=True)

    def _read_entries(self) -> List[ConfigLineEntry]:
        if not self._env_path.exists():
            return []
        return [
            ConfigLineEntry.parse(raw_line)
            for raw_line in _split_env_lines(self._env_path.read_text(encoding="utf-8"))
        ]

    @staticmethod
    def _find_last_key_indexes(entries: List[ConfigLineEntry]) -> Dict[str, int]:
        key_to_index: Dict[str, int] = {}
        for index, entry in enumerate(entries):
            if entry.kind != "assignment" or entry.key is None:
                continue
            key_to_index[entry.key.upper()] = index

        return key_to_index

    @staticmethod
    def _resolve_env_path() -> Path:
        env_file = os.getenv("ENV_FILE")
        if env_file:
            return Path(env_file).resolve()

        return (Path(__file__).resolve().parent.parent.parent / ".env").resolve()
