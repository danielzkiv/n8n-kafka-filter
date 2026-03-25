.PHONY: up down logs build test produce-dev produce-stage produce-prod shell-kafka

# ── Local dev ────────────────────────────────────────────────────────────────

up:
	docker-compose build
	docker-compose up -d
	@echo ""
	@echo "  Kafka UI   → http://localhost:8081"
	@echo "  n8n        → http://localhost:5678"
	@echo "  Forwarder  → http://localhost:8080"
	@echo ""
	@echo "Run 'make logs' to follow forwarder logs"

down:
	docker-compose down -v

restart:
	docker-compose restart forwarder

logs:
	docker-compose logs -f forwarder

logs-all:
	docker-compose logs -f

build:
	docker-compose build forwarder

health:
	curl -s http://localhost:8080/health | python -m json.tool
	@echo ""
	curl -s http://localhost:8080/metrics | python -m json.tool

# ── Test events ──────────────────────────────────────────────────────────────

produce-dev:
	python scripts/produce_event.py --topic dev-events --event-type order.created --payload '{"amount": 150, "customer_id": "CUST-1"}'

produce-stage:
	python scripts/produce_event.py --topic stage-events --event-type payment.completed --payload '{"amount": 99, "status": "success"}'

produce-prod:
	python scripts/produce_event.py --topic prod-events --event-type order.created --payload '{"amount": 2000, "customer_id": "CUST-VIP"}'

produce-sample:
	python scripts/produce_event.py --topic dev-events --file scripts/sample_events/order_created.json
	python scripts/produce_event.py --topic dev-events --file scripts/sample_events/payment_completed.json

# ── Tests ────────────────────────────────────────────────────────────────────

test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v --cov=app --cov-report=term-missing

# ── Kafka shell ───────────────────────────────────────────────────────────────

shell-kafka:
	docker-compose exec kafka bash

topics:
	docker-compose exec kafka kafka-topics.sh --bootstrap-server localhost:9092 --list
