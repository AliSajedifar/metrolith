"""Non-critical message-broker evidence from the canonical inventory."""

from __future__ import annotations

import re

from modules.inventory import RepositoryInventory


BROKER_PATTERNS = {
    "activemq": re.compile(r"\bactivemq\b", re.I),
    "aws_sqs": re.compile(r"\b(?:aws[-_. /]?sqs|amazon[-_. /]?sqs|sqsclient)\b", re.I),
    "google_pubsub": re.compile(r"\b(?:google[-_. /]?cloud[-_. /]?pubsub|pubsubclient)\b", re.I),
    "kafka": re.compile(r"\b(?:apache[-_. /]?kafka|kafka)\b", re.I),
    "nats": re.compile(r"\b(?:nats[-_. /]?io|nats[-_. /]?(?:server|client|streaming)|jetstream)\b", re.I),
    "pulsar": re.compile(r"\b(?:apache[-_. /]?pulsar|pulsar[-_. /]?client)\b", re.I),
    "rabbitmq": re.compile(r"\b(?:rabbitmq|amqp[-_. /]?(?:client|connection))\b", re.I),
    "redis_streams": re.compile(r"\b(?:xadd|xreadgroup|redis[-_. /]?streams?)\b", re.I),
}


def detect_message_brokers(repo_path, inventory: RepositoryInventory | None = None):
    inventory = inventory or RepositoryInventory(repo_path)
    evidence = []
    seen = set()
    for record in inventory:
        if record.exclusion_reason is not None:
            continue
        content = inventory.read_text(record)
        if not content:
            continue
        for broker, pattern in BROKER_PATTERNS.items():
            match = pattern.search(content)
            key = (broker, record.relative_path)
            if match and key not in seen:
                seen.add(key)
                evidence.append(
                    {
                        "broker": broker,
                        "file": record.relative_path,
                        "matched_marker": match.group(0),
                    }
                )
    evidence.sort(key=lambda item: (item["broker"], item["file"], item["matched_marker"]))
    return {
        "detected": bool(evidence),
        "brokers": sorted({item["broker"] for item in evidence}),
        "evidence": evidence,
        "analysis_kind": "static marker evidence; runtime configuration was not executed",
    }
