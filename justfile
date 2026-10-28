set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

install:
    uv sync

test:
    uv run pytest -q

lint:
    uv run ruff check .
    uv run ruff format --check .

fmt:
    uv run ruff check --fix .
    uv run ruff format .

build:
    uv build
    uvx twine check dist/*
