# bookpress — development and conversion entry points.
#
# Run `make` (or `make help`) to list targets. Everything runs locally; nothing
# is downloaded except by `make venv` / `make dist`, which use pip.

PY      ?= .venv/bin/python
BP      := $(PY) -m bookpress.cli
WORK    := $(CURDIR)/.selftest-work
FIXTURE := $(WORK)/scan.pdf

.DEFAULT_GOAL := help
.PHONY: help venv install health test check lint fixture book review clean distclean dist

help: ## show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "  variables:  PY=$(PY)  (override: make test PY=python3)"

venv: ## create .venv and install bookpress into it
	@test -d .venv || python3 -m venv .venv
	.venv/bin/pip install --upgrade pip >/dev/null
	.venv/bin/pip install -e .
	@echo "now run: make health"

install: ## install the package into the active python (pip install -e .)
	$(PY) -m pip install -e .

health: ## report which tools/libraries are usable here
	$(BP) healthcheck

test: ## end-to-end test: synthetic scan -> full pipeline -> assertions
	$(PY) tests/selftest.py

lint: ## byte-compile everything, plus ruff if it is installed
	$(PY) -m compileall -q bookpress tests
	@command -v ruff >/dev/null 2>&1 && ruff check bookpress tests || \
	  echo "ruff not installed - skipped (byte-compile passed)"

check: lint health test ## what CI runs

fixture: ## build the synthetic scan and its EPUB/PDF for eyeballing
	@mkdir -p $(WORK)
	$(PY) -c "from pathlib import Path; from tests.make_fixture import build; build(Path('$(FIXTURE)'))"
	@echo "scan: $(FIXTURE)"
	@echo "convert it with: make book PDF=$(FIXTURE) SLUG=fixture TITLE='A Book of No Import' AUTHOR='An Author'"

book: ## convert a scan: make book PDF=inbox/x.pdf SLUG=my TITLE='Title' AUTHOR='Author'
	@test -n "$(PDF)"  || { echo "usage: make book PDF=inbox/x.pdf SLUG=mybook TITLE='Title' AUTHOR='Author' [SERIES=.. PAGE=a5 PT=11]"; exit 2; }
	@test -n "$(SLUG)" || { echo "SLUG is required"; exit 2; }
	$(BP) add --pdf "$(PDF)" --slug $(SLUG) --title "$(TITLE)" --author "$(AUTHOR)" \
	          $(if $(SERIES),--series "$(SERIES)") --page $(or $(PAGE),a5) --pt $(or $(PT),11)
	$(BP) all $(SLUG)
	$(BP) status $(SLUG)
	@echo "outputs: books/$(SLUG)/epub/  books/$(SLUG)/pdf/"

review: ## audit trail for a book: make review SLUG=mybook
	@test -n "$(SLUG)" || { echo "usage: make review SLUG=mybook"; exit 2; }
	$(BP) status $(SLUG)
	$(BP) review $(SLUG)
	$(BP) words $(SLUG)

clean: ## remove caches and build artefacts (never touches books/ or inbox/)
	rm -rf build dist *.egg-info .pytest_cache
	find bookpress tests -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(WORK)

distclean: clean ## clean, plus the virtualenv
	rm -rf .venv

dist: ## build an sdist + wheel (needs: pip install build)
	$(PY) -m build
