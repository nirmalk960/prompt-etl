# Prompt ETL convenience commands.
SHELL := /bin/bash

.PHONY: help up down logs build rebuild ps api worker migrate revision shell-api shell-worker \
        test fmt lint frontend-install frontend-dev clean

help:
	@echo "Prompt ETL dev commands:"
	@echo "  make up             - start the full stack (api, worker, postgres, redis, minio, ollama, frontend)"
	@echo "  make down           - stop everything"
	@echo "  make logs           - tail logs for all services"
	@echo "  make build          - build all images"
	@echo "  make rebuild        - build with --no-cache"
	@echo "  make ps             - list running services"
	@echo "  make migrate        - apply DB migrations"
	@echo "  make revision m=... - create a new alembic revision"
	@echo "  make shell-api      - shell into the api container"
	@echo "  make shell-worker   - shell into the worker container"
	@echo "  make test           - run backend tests"
	@echo "  make fmt            - format backend with ruff"
	@echo "  make lint           - lint backend"
	@echo "  make clean          - remove local data volumes (DESTRUCTIVE)"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

build:
	docker compose build

rebuild:
	docker compose build --no-cache

ps:
	docker compose ps

api:
	docker compose up -d api

worker:
	docker compose up -d worker

migrate:
	docker compose run --rm api alembic upgrade head

revision:
	@if [ -z "$(m)" ]; then echo "Usage: make revision m='describe change'"; exit 1; fi
	docker compose run --rm api alembic revision --autogenerate -m "$(m)"

shell-api:
	docker compose exec api bash

shell-worker:
	docker compose exec worker bash

test:
	docker compose run --rm api pytest -q

fmt:
	docker compose run --rm api ruff format prompt_etl tests

lint:
	docker compose run --rm api ruff check prompt_etl tests

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

clean:
	docker compose down -v
	rm -rf minio-data postgres-data redis-data ollama-data
