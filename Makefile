PYTHON ?= python3
TEST_ENV ?= .venv-test
TEST_PYTHON = $(TEST_ENV)/bin/python

.PHONY: setup-test test test-unit test-integration smoke-test release windows-launchers package-windows-agent

setup-test:
	$(PYTHON) -m venv "$(TEST_ENV)"
	"$(TEST_PYTHON)" -m pip install -r requirements-test.txt

test: windows-launchers
	"$(TEST_PYTHON)" -B run_tests.py --suite all

test-unit: windows-launchers
	"$(TEST_PYTHON)" -B run_tests.py --suite unit

test-integration:
	"$(TEST_PYTHON)" -B run_tests.py --suite integration

smoke-test:
	"$(TEST_PYTHON)" -B smoke_test.py

windows-launchers:
	$(PYTHON) -B -m release_tools.windows

release: windows-launchers
	"$(TEST_PYTHON)" -B build_release.py --output-directory dist

package-windows-agent:
	$(PYTHON) -B build_agent_package.py
