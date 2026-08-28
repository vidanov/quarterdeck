.PHONY: bootstrap verify test build help clean

help:
	@echo "bootstrap  — set up venv and install all dependencies"
	@echo "verify     — run tests and build frontend (matches CI)"
	@echo "test       — run backend tests only"
	@echo "build      — build frontend bundle only"

# Set up the project from scratch
bootstrap:
	python3 -m venv venv
	venv/bin/pip install -r requirements.txt
	npm --prefix frontend ci

# Run all checks (matches CI)
verify: venv
	venv/bin/python -m pytest tests/ -q
	npm --prefix frontend run build

# Tests only
test: venv
	venv/bin/python -m pytest tests/ -q

# Create venv and install deps if missing
venv:
	python3 -m venv venv
	venv/bin/pip install -r requirements.txt

# Build frontend bundle
build:
	npm --prefix frontend run build
