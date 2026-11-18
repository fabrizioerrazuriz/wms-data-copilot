PSQL := psql postgresql://wms:wms@localhost:5433/wms

.PHONY: help install bootstrap ask quality eval test lint api up down logs psql clean

help: ## Muestra los comandos disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Instala el paquete en modo editable con dependencias de desarrollo
	python -m pip install -e ".[dev]"

bootstrap: ## Genera datos sinteticos, carga en la base e indexa la base de conocimiento
	python -m wms_copilot.cli bootstrap

ask: ## Pregunta al copiloto: make ask Q="¿Qué bodegas tienen saldo negativo?"
	python -m wms_copilot.cli ask "$(Q)"

quality: ## Escaneo de reglas de calidad de datos
	python -m wms_copilot.cli quality

eval: ## Corre el golden set y aplica el gate (falla el comando si baja el umbral)
	python -m wms_copilot.cli eval --min-pass 0.6

test: ## Tests unitarios y de integracion
	python -m pytest

lint: ## Lint con ruff
	python -m ruff check .

api: ## Levanta la API en http://127.0.0.1:8000/docs
	python -m wms_copilot.cli serve --reload

up: ## Levanta API + PostgreSQL con Docker Compose
	docker compose up --build -d

down: ## Baja los contenedores
	docker compose down

logs: ## Logs de la API
	docker compose logs -f api

psql: ## Consola SQL contra el PostgreSQL del compose
	$(PSQL)

clean: ## Borra datos generados y artefactos locales (no toca el codigo)
	rm -rf data artifacts .pytest_cache .ruff_cache
