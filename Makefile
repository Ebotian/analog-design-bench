PY ?= python3

.PHONY: fetch-t0 check manifest test types corners p1 results legality docs docs-check hooks ci clean

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

results:
	$(PY) scripts/reconcile_verifiers.py

legality:
	$(PY) scripts/replay_legality.py --checker-sample 3600 --jobs 12

docs:
	$(PY) scripts/build_results_md.py
	$(PY) scripts/build_docs.py

docs-check:
	$(PY) scripts/build_results_md.py --check
	$(PY) scripts/build_docs.py --check

hooks:
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-commit
	@echo "git hooks installed: core.hooksPath=.githooks"

ci: test types corners results docs-check

clean:
	rm -rf .pytest_cache __pycache__ src/*/__pycache__ scripts/__pycache__
