.PHONY: bootstrap verify test build

# Set up the project from scratch
bootstrap:
	python3 -m venv venv
	venv/bin/pip install -r requirements.txt
	npm --prefix frontend ci

# Run all checks (matches CI)
verify:
	venv/bin/python -m pytest tests/ -q
	npm --prefix frontend run build

# Tests only
test:
	venv/bin/python -m pytest tests/ -q

# Build frontend bundle
build:
	npm --prefix frontend run build
