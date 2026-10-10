PYTHON ?= python3
TEST_ENV ?= .venv-test
TEST_PYTHON = $(TEST_ENV)/bin/python

.PHONY: setup-test test test-unit test-integration smoke-test release package-windows-agent

setup-test:
	$(PYTHON) -m venv "$(TEST_ENV)"
	"$(TEST_PYTHON)" -m pip install -r requirements-test.txt

test:
	"$(TEST_PYTHON)" -B run_tests.py --suite all

test-unit:
	"$(TEST_PYTHON)" -B run_tests.py --suite unit

test-integration:
	"$(TEST_PYTHON)" -B run_tests.py --suite integration

smoke-test:
	"$(TEST_PYTHON)" -B smoke_test.py

release:
	"$(TEST_PYTHON)" -B build_release.py --output-directory dist

package-windows-agent:
	$(PYTHON) -B build_agent_package.py
