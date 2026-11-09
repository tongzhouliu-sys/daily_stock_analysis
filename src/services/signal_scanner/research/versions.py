import hashlib
import platform
from importlib.metadata import version
from copy import deepcopy
from pathlib import Path

from src.services.signal_scanner.core import digest, require
from src.services.signal_scanner.domain.features import ALGORITHM
from src.services.signal_scanner.domain.rule_parameters import DEFAULT_PARAMETERS, parameters_for
from src.services.signal_scanner.domain.rules import RULESET

ENGINE_VERSION = "research-1.0.0"
STATISTICS_VERSION = "linear-quantiles-1.0.0"
HASH_VERSION = "semantic-times-1.0.0"


def code_hash():
    root = Path(__file__).resolve().parents[1]
    return digest(
        {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(p for p in root.rglob("*") if p.suffix in (".py", ".sql"))
        }
    )


def dependency_hash():
    return digest(dependency_manifest())


def dependency_manifest():
    root = Path(__file__).resolve().parents[1] / "upstream"
    return {
        "source_specs": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("requirements.lock", "pyproject.toml")
        },
        "python": platform.python_version(),
        "runtime": {name: version(name) for name in (
            "fastapi", "pydantic", "httpx", "exchange-calendars", "pandas", "numpy", "argon2-cffi",
        )},
    }


def ruleset(overrides=None):
    overrides = overrides or {}
    require(not set(overrides) - set(RULESET), "UNKNOWN_RULE", status=422)
    result = deepcopy(RULESET)
    for rule, row in result.items():
        row["parameters"] = parameters_for(rule, overrides.get(rule))
        row["version"] = (
            "1.0.0"
            if row["parameters"] == DEFAULT_PARAMETERS[rule]
            else ("1.0.0+" + digest(row["parameters"])[:16])
        )
    return result


def rules_version(overrides=None):
    return "rules-" + digest(ruleset(overrides))


def verify_versions(m, cal):
    require(
        m["algorithm_version"] == ALGORITHM and m["engine_version"] == ENGINE_VERSION, "VERSION_UNAVAILABLE"
    )
    require(m["calendar_version"] == cal.version, "VERSION_UNAVAILABLE")
    require(m["ruleset_version"] == rules_version(m.get("parameter_overrides")), "VERSION_UNAVAILABLE")
    require(
        m["code_hash"] == code_hash() and m["dependency_hash"] == dependency_hash(), "VERSION_UNAVAILABLE"
    )
