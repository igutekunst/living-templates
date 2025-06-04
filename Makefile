.PHONY: help install install-dev test lint format clean examples

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install the package
	pip install .

install-dev:  ## Install in development mode with dev dependencies
	pip install -e ".[dev]"

test:  ## Run tests
	pytest tests/ -v

test-cov:  ## Run tests with coverage
	pytest tests/ -v --cov=living_templates --cov-report=html --cov-report=term

lint:  ## Run linting
	flake8 src/living_templates tests/
	mypy src/living_templates

format:  ## Format code
	black src/living_templates tests/
	isort src/living_templates tests/

format-check:  ## Check code formatting
	black --check src/living_templates tests/
	isort --check-only src/living_templates tests/

clean:  ## Clean build artifacts
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

examples:  ## Run example templates
	@echo "Running hello world example..."
	living-templates validate examples/hello-world.yaml
	lt -s examples/hello-world.yaml ./hello.txt --input name="Developer"
	@echo "Generated file:"
	cat hello.txt
	@echo ""
	@echo "Running config template example..."
	living-templates validate examples/config-template.yaml
	lt -s examples/config-template.yaml ./app-config.json \
		--input app_name="TestApp" \
		--input port=3000 \
		--input debug=true \
		--input features='["auth", "logging"]' \
		--input database_config="examples/database.json"
	@echo "Generated config:"
	cat app-config.json

build:  ## Build package
	python -m build

upload-test:  ## Upload to test PyPI
	python -m twine upload --repository testpypi dist/*

upload:  ## Upload to PyPI
	python -m twine upload dist/*

dev-setup:  ## Set up development environment
	pip install -e ".[dev]"
	pre-commit install

daemon-start:  ## Start the daemon
	living-templates daemon start

daemon-stop:  ## Stop the daemon
	living-templates daemon stop

daemon-status:  ## Check daemon status
	living-templates daemon status 