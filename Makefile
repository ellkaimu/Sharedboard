PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
PORT ?= 8888

.PHONY: install run test lint clean deb

install:
	$(PYTHON) -m venv .venv
	. .venv/bin/activate && $(PIP) install -U pip && $(PIP) install -e ".[test]"

run:
	SHAREBOARD_PORT=$(PORT) $(PYTHON) -m shareboard

test:
	$(PYTHON) -m pytest -q tests/

lint:
	$(PYTHON) -m compileall -q shareboard/

deb:
	./build_deb.sh

clean:
	rm -rf build dist *.egg-info .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
