"""The policy document: a versioned, explicit statement of what is required.

A policy is data, not code. It names the rules it enables, may override a
severity, and may carry **bounded** waivers.

**Waivers are bounded by construction.** Every waiver needs a reason and an
expiry date. An unbounded waiver is indistinguishable from deleting the rule,
and it decays into a permanent exception nobody remembers agreeing to. An
expired waiver does not apply and is reported as expired, so the finding
reappears rather than staying silently suppressed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from modules.policy import rules as rule_module
from archlens_json import (
    POLICY_JSON_LIMITS,
    StrictJsonError,
    load_file,
    validate_json_value,
)

POLICY_DOCUMENT_FORMAT_VERSION = "1.0.0"


class PolicyDocumentInvalid(ValueError):
    """The policy itself is malformed. Never silently repaired."""


def _policy_file_error(path: Path, error: StrictJsonError) -> PolicyDocumentInvalid:
    if error.code == "artifact_unreadable":
        return PolicyDocumentInvalid(f"cannot read policy {path}: {error.message}")
    if error.code == "json_malformed":
        return PolicyDocumentInvalid(f"{path} is not valid JSON: {error.message}")
    return PolicyDocumentInvalid(str(error))


@dataclass(frozen=True)
class Waiver:
    """One bounded exception, scoped as narrowly as the author wrote it."""

    rule_id: str
    reason: str
    expires_on: date
    subject_key: str | None = None
    issue_id: str | None = None

    def applies_to(self, finding: rule_module.Finding, today: date) -> bool:
        if finding.rule_id != self.rule_id:
            return False
        if self.subject_key is not None and finding.subject_key != self.subject_key:
            return False
        return not self.expired(today)

    def expired(self, today: date) -> bool:
        return today > self.expires_on

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "reason": self.reason,
            "expires_on": self.expires_on.isoformat(),
            "subject_key": self.subject_key,
            "issue_id": self.issue_id,
        }


@dataclass
class PolicyDocument:
    name: str
    enabled_rules: dict[str, str]
    waivers: tuple[Waiver, ...] = ()
    expected_contracts: dict[str, str] = field(default_factory=dict)
    description: str = ""

    def severity_for(self, rule_id: str) -> str | None:
        return self.enabled_rules.get(rule_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_document_format_version": POLICY_DOCUMENT_FORMAT_VERSION,
            "name": self.name,
            "description": self.description,
            "rules": dict(sorted(self.enabled_rules.items())),
            "expected_contracts": dict(sorted(self.expected_contracts.items())),
            "waivers": [waiver.as_dict() for waiver in self.waivers],
        }


def default_policy() -> PolicyDocument:
    """Every rule that is meaningful without an explicit opt-in.

    `measurement.incomplete` and `reproducibility.provenance_incomplete` are
    deliberately off: partial measurement and a dirty development tree are
    honest, normal outcomes, and a default that failed on them would train
    everyone to ignore the policy.
    """
    return PolicyDocument(
        name="archlens-default-integrity-v1",
        description=(
            "Integrity, diagnostic and reproducibility rules only. No metric "
            "thresholds: Policy v2 remains blocked on differential-validation "
            "evidence."
        ),
        enabled_rules={
            rule.identifier: rule.default_severity
            for rule in rule_module.RULES
            if rule.default_enabled
        },
    )


def _parse_date(value: Any, where: str) -> date:
    if not isinstance(value, str):
        raise PolicyDocumentInvalid(f"{where}: expires_on must be a YYYY-MM-DD string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PolicyDocumentInvalid(f"{where}: {exc}") from None


def load_policy(payload: Any) -> PolicyDocument:
    """Build a policy from a parsed document, refusing anything malformed."""
    try:
        validate_json_value(
            payload, source="policy document", limits=POLICY_JSON_LIMITS, expect=dict
        )
    except StrictJsonError as exc:
        raise PolicyDocumentInvalid(str(exc)) from None
    if not isinstance(payload, dict):
        raise PolicyDocumentInvalid("a policy document must be a JSON object")

    declared = payload.get("policy_document_format_version")
    if declared != POLICY_DOCUMENT_FORMAT_VERSION:
        raise PolicyDocumentInvalid(
            f"policy_document_format_version must be "
            f"{POLICY_DOCUMENT_FORMAT_VERSION!r}; got {declared!r}"
        )

    name = payload.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PolicyDocumentInvalid("a policy document must have a non-empty name")

    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, dict) or not raw_rules:
        raise PolicyDocumentInvalid("a policy document must enable at least one rule")

    enabled: dict[str, str] = {}
    for rule_id, severity in raw_rules.items():
        if rule_id not in rule_module.RULES_BY_ID:
            raise PolicyDocumentInvalid(
                f"unknown rule {rule_id!r}; known rules: "
                f"{', '.join(sorted(rule_module.RULES_BY_ID))}"
            )
        if severity not in rule_module.SEVERITIES:
            raise PolicyDocumentInvalid(
                f"rule {rule_id!r} has severity {severity!r}; expected one of "
                f"{', '.join(rule_module.SEVERITIES)}"
            )
        enabled[rule_id] = severity

    raw_waivers = payload["waivers"] if "waivers" in payload else []
    if not isinstance(raw_waivers, list):
        raise PolicyDocumentInvalid("waivers must be an array")
    waivers: list[Waiver] = []
    for index, raw in enumerate(raw_waivers):
        where = f"waivers[{index}]"
        if not isinstance(raw, dict):
            raise PolicyDocumentInvalid(f"{where}: must be an object")
        rule_id = raw.get("rule_id")
        if rule_id not in rule_module.RULES_BY_ID:
            raise PolicyDocumentInvalid(f"{where}: unknown rule {rule_id!r}")
        reason = raw.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise PolicyDocumentInvalid(
                f"{where}: a waiver must state a reason. An unexplained waiver "
                f"is indistinguishable from deleting the rule."
            )
        if "expires_on" not in raw:
            raise PolicyDocumentInvalid(
                f"{where}: a waiver must have an expiry. An unbounded waiver "
                f"decays into a permanent exception nobody agreed to."
            )
        subject_key = raw.get("subject_key")
        if subject_key is not None and not isinstance(subject_key, str):
            raise PolicyDocumentInvalid(f"{where}: subject_key must be a string")
        issue_id = raw.get("issue_id")
        if issue_id is not None and not isinstance(issue_id, str):
            raise PolicyDocumentInvalid(f"{where}: issue_id must be a string")
        waivers.append(Waiver(
            rule_id=rule_id, reason=reason,
            expires_on=_parse_date(raw.get("expires_on"), where),
            subject_key=subject_key,
            issue_id=issue_id,
        ))

    expected = (
        payload["expected_contracts"] if "expected_contracts" in payload else {}
    )
    if not isinstance(expected, dict):
        raise PolicyDocumentInvalid("expected_contracts must be an object")
    for key, value in expected.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise PolicyDocumentInvalid(
                "expected_contracts keys and values must be strings"
            )

    description = payload["description"] if "description" in payload else ""
    if not isinstance(description, str):
        raise PolicyDocumentInvalid("description must be a string")

    return PolicyDocument(
        name=name.strip(),
        description=description,
        enabled_rules=enabled,
        waivers=tuple(waivers),
        expected_contracts=dict(expected),
    )


def load_policy_file(path: Path) -> PolicyDocument:
    try:
        payload = load_file(
            path, source=str(path), limits=POLICY_JSON_LIMITS, expect=dict
        )
    except StrictJsonError as exc:
        raise _policy_file_error(Path(path), exc) from None
    return load_policy(payload)
