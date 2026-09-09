MKFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
PROJECT_DIR := $(dir $(MKFILE_PATH))

INFRA_LOCAL := $(PROJECT_DIR)infra/
DOCKER_COMPOSE_LOCAL_FILE := $(INFRA_LOCAL)docker-compose.dev.yaml

DOTENV_DIR := $(PROJECT_DIR)dotenv/
DOTENV_LOCAL := $(DOTENV_DIR).env.local

COMPOSE ?= docker compose
CONTAINER_ENGINE ?= docker

IMAGE_NAME := knowledgebase-storage
TAG_NAME := $(if $(TAG_NAME),$(TAG_NAME),local)

.PHONY: init dev-up dev-down api worker migrate revision frontend-dev frontend-build docker-build lint

init:
	uv sync
	cd frontend && pnpm install
	@test -f $(DOTENV_LOCAL) || cp $(DOTENV_DIR).env.example $(DOTENV_LOCAL)

dev-up:
	$(COMPOSE) -f $(DOCKER_COMPOSE_LOCAL_FILE) up -d

dev-down:
	$(COMPOSE) -f $(DOCKER_COMPOSE_LOCAL_FILE) down

api:
	uv run python -m backend

worker:
	uv run python -m backend.cli worker --api-url http://127.0.0.1:8006


migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(MESSAGE)"

frontend-dev:
	cd frontend && pnpm run dev

frontend-build:
	cd frontend && pnpm run build
	mkdir -p backend/frontend && cp frontend/dist/index.html backend/frontend/index.html

docker-build:
	$(CONTAINER_ENGINE) build -f dockerfiles/backend.dockerfile -t $(IMAGE_NAME):$(TAG_NAME) .

lint:
	uv run ruff check backend hooks
	uv run ruff format --check backend hooks
	uv run mypy backend
