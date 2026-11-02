PY ?= python3

.PHONY: fetch-t0 check manifest test types corners p1 ci clean

fetch-t0:
	$(PY) scripts/fetch_t0.py --tasks-only
	$(PY) scripts/fetch_t0.py --workers 6

check:
	$(PY) scripts/check_t0.py --write-manifest

manifest:
	$(PY) scripts/check_t0.py --write-manifest --no-verify

test:
	$(PY) -m pytest -q

types:
	$(PY) -m mypy

corners:
	$(PY) scripts/build_corners.py --check

p1:
	$(PY) scripts/check_p1.py

ci: test types corners

clean:
	rm -rf .pytest_cache __pycache__ src/*/__pycache__ scripts/__pycache__
