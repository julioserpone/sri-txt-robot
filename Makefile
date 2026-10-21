.PHONY: help install check test lint format type robot clean

PYTHON ?= python3
VENV   ?= .venv
BIN     = $(VENV)/bin

help: ## Muestra esta ayuda.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

install: $(BIN)/python ## Instala el paquete y sus dependencias de desarrollo.
	$(BIN)/pip install -e ".[dev]"
	$(BIN)/playwright install chromium

test: ## Ejecuta las pruebas unitarias.
	$(BIN)/pytest

lint: ## Verifica estilo y tipos (sin modificar archivos).
	$(BIN)/ruff check .
	$(BIN)/mypy

format: ## Corrige el estilo automáticamente.
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

type: ## Solo análisis estático.
	$(BIN)/mypy

check: lint test ## Ejecuta lint, tipos y pruebas.

robot: ## Ejecuta el CLI (usa ./robot <comando>).
	$(BIN)/sri-txt-robot $(ARGS)

clean: ## Elimina artefactos locales.
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
