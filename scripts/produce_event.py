#!/usr/bin/env python3
"""
Send a test event to a local Kafka topic.

Usage:
  python scripts/produce_event.py --topic dev-events --event-type order.created
  python scripts/produce_event.py --topic prod-events --event-type payment.completed --payload '{"amount": 500}'
  python scripts/produce_event.py --topic dev-events --file scripts/sample_events/order_created.json
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone

try:
    from kafka import KafkaProducer
except ImportError:
    print("Install kafka-python first: pip install kafka-python")
    sys.exit(1)


BOOTSTRAP_SERVERS = "localhost:9093"  # External listener exposed by docker-compose


def build_event(event_type: str, extra_payload: dict) -> dict:
    return {
        "event_id": f"EVT-{uuid.uuid4().hex[:8].upper()}",
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": extra_payload,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Produce a test Kafka event")
    parser.add_argument("--topic", required=True, help="Target topic (e.g. dev-events)")
    parser.add_argument("--event-type", default="test.event", help="Value for the event_type field")
    parser.add_argument("--payload", default="{}", help="JSON string merged into event.payload")
    parser.add_argument("--file", help="Send a raw JSON file instead of building an event")
    parser.add_argument("--count", type=int, default=1, help="Number of events to send")
    parser.add_argument("--broker", default=BOOTSTRAP_SERVERS, help=f"Kafka broker (default: {BOOTSTRAP_SERVERS})")
    args = parser.parse_args()

    producer = KafkaProducer(
        bootstrap_servers=args.broker,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )

    for i in range(args.count):
        if args.file:
            with open(args.file) as f:
                event = json.load(f)
        else:
            extra = json.loads(args.payload)
            event = build_event(args.event_type, extra)

        future = producer.send(args.topic, value=event)
        record = future.get(timeout=10)
        print(f"[{i+1}/{args.count}] Sent to {record.topic}[{record.partition}] offset={record.offset}")
        print(f"  {json.dumps(event, indent=2)}")

    producer.flush()
    producer.close()


if __name__ == "__main__":
    main()
