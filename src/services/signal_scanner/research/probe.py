from src.services.signal_scanner.core import DomainError, digest
from src.services.signal_scanner.domain.features import ALGORITHM
from src.services.signal_scanner.domain.rule_parameters import parameters_for
from src.services.signal_scanner.domain.rules import RULESET
from src.services.signal_scanner.research.isolation import offline


def probe_snapshot(files, scan, cal):
    if not scan:
        return dict(status="SKIPPED", legacy_status="SKIPPED", reason="NO_ELIGIBLE_SNAPSHOT")
    try:
        manifest = files.read(scan["input_manifest_ref"])
        expected = files.read(scan["output_manifest_ref"])
        versions = manifest.get("rules", {}).get("value", {}).get("rules", {})
        available = (
            manifest.get("algorithm_version") == ALGORITHM
            and manifest.get("calendar_version") == cal.version
            and all(
                versions.get(k, {}).get("version") == v["version"]
                and parameters_for(k, versions.get(k, {}).get("parameters")) == parameters_for(k)
                for k, v in RULESET.items()
            )
        )
        if not available:
            return dict(status="VERSION_UNAVAILABLE", legacy_status="SKIPPED", reason="VERSION_UNAVAILABLE")
        from src.services.signal_scanner.worker.pipeline import business_view, calculate

        with offline() as observed:
            actual = digest(business_view(calculate(manifest, cal)))
        passed = actual == expected["business_output_hash"]
        return dict(
            status="PASS" if passed else "FAIL",
            legacy_status="PASS" if passed else "FAIL",
            reason=None if passed else "REPLAY_MISMATCH",
            input_hash=digest(manifest),
            expected_hash=expected["business_output_hash"],
            actual_hash=actual,
            network_calls=observed["network_attempts"],
            account_writes=observed["forbidden_writes"],
            difference=None if passed else dict(expected=expected["business_output_hash"], actual=actual),
        )
    except DomainError as ex:
        return dict(status="FAIL", legacy_status="FAIL", reason=ex.code)
