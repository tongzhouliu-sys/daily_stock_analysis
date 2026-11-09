from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, getcontext
from pathlib import Path
from typing import Any

getcontext().prec = 28
UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[3]


class DomainError(Exception):
    def __init__(self, code: str, message: str = "", status: int = 409, **details: Any):
        self.code, self.message, self.status, self.details = code, message or code, status, details
        super().__init__(self.message)


def now() -> datetime:
    return datetime.now(UTC)


def stamp(value: datetime | None = None) -> str:
    return (value or now()).astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def instant(value: str | datetime) -> datetime:
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if d.tzinfo is None:
            raise ValueError("Timezone required")
        return d.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        raise DomainError("VALIDATION_ERROR", "时间必须带时区", 422) from None


def dec(value: Any, *, nonnegative: bool = False) -> Decimal:
    if not isinstance(value, (str, Decimal, int)) or isinstance(value, bool):
        raise DomainError("VALIDATION_ERROR", "金额和价格必须为十进制字符串", 422)
    try:
        if isinstance(value, str) and not re.fullmatch(r"-?\d+(\.\d+)?", value):
            raise ValueError()
        d = Decimal(value)
        if not d.is_finite() or len(d.as_tuple().digits) > 28 or (nonnegative and d < 0):
            raise ValueError()
        return d
    except (InvalidOperation, ValueError):
        raise DomainError("VALIDATION_ERROR", "十进制数超出允许范围", 422) from None


def text_decimal(d: Decimal | int | str) -> str:
    value = Decimal(d)
    if not value.is_finite():
        raise ValueError("Non-finite decimal")
    s = format(value, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return text_decimal(value)
    if isinstance(value, datetime):
        return stamp(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_encode, allow_nan=False
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def uid(prefix: str = "") -> str:
    return f"{prefix}_{uuid.uuid4().hex}" if prefix else str(uuid.uuid4())


def require(condition: Any, code: str, message: str = "", status: int = 409, **details: Any) -> None:
    if not condition:
        raise DomainError(code, message, status, **details)


def reject_credentials(value: Any) -> None:
    forbidden = {
        "password",
        "api_key",
        "api_secret",
        "secret",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "private_key",
    }
    if isinstance(value, dict):
        require(
            not {str(k).lower() for k in value} & forbidden,
            "VALIDATION_ERROR",
            "凭据不得进入业务数据或快照",
            422,
        )
        for item in value.values():
            reject_credentials(item)
    elif isinstance(value, list):
        for item in value:
            reject_credentials(item)


@dataclass
class Settings:
    runtime: Path
    origin: str = "http://testserver"
    secure_cookie: bool = False

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            Path(os.environ.get("SCANNER_RUNTIME") or Path(os.environ.get("DATABASE_PATH") or "./data/stock_analysis.db").resolve().parent / "signal-scanner").resolve(),
            os.environ.get("SCANNER_ORIGIN", "http://testserver"),
            os.environ.get("SCANNER_HTTPS", "false").lower() == "true",
        )

    @property
    def db_path(self) -> Path:
        return self.runtime / "scanner.sqlite3"
