# Shared recipes for working on Python projects.

PY_MAKE_ORIGIN := https://raw.githubusercontent.com/pyranha-labs/build-tools/refs/heads/main/python.mk
PY_PROJECT_NAME := $(shell sed -n 's/^name = "\(.*\)"$$/\1/p' pyproject.toml)
PY_PROJECT_ROOT := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
PYTHON_BIN := python3.12
PYLINT_EXTRAS :=

##### Development Setups and Configurations #####

# Update the shared python recipes (this file) outside initial setup.
update-py-make:
	curl $(PY_MAKE_ORIGIN) -o python.mk

# Create python virtual environment for development/testing.
.PHONY: venv
venv:
	$(PYTHON_BIN) -m venv $(PY_PROJECT_ROOT).venv && \
	ln -sfnv $(PY_PROJECT_ROOT).venv/bin/activate $(PY_PROJECT_ROOT)activate && \
	. $(PY_PROJECT_ROOT)activate && \
	pip install $(shell ls requirements*.txt | tr '\n' ' ' | sed 's/\(requirements[^ ]*\)/-r \1/g') && \
	echo $(PY_PROJECT_ROOT) > $(PY_PROJECT_ROOT).venv/lib/$(PYTHON_BIN)/site-packages/$(PY_PROJECT_NAME).pth
	@. $(PY_PROJECT_ROOT)activate && pip check && \
		echo "🏆 Virtual environment built successfully!" || \
		(echo "💔 Virtual environment set up failed, resolve errors and try again."; exit 1)

# Clean the python virtual environment.
.PHONY: clean-venv
clean-venv:
	-rm -r $(PY_PROJECT_ROOT)activate $(PY_PROJECT_ROOT).venv

##### Quality Assurance #####

# Check source code format for consistent patterns.
.PHONY: format
format:
	@echo Running code format checks: black/ruff
	@ruff format --check --diff $(PY_PROJECT_ROOT) && \
		echo "🏆 Code format good to go!" || \
		(echo "💔 Please run formatter to ensure code consistency and quality:\nruff format $(PY_PROJECT_ROOT)"; exit 1)

# Check for common lint/complexity/style issues.
# Ruff is used for isort, pycodestyle, pydocstyle. Pylint is used separately for greater coverage.
.PHONY: lint
lint:
	@echo Running code and documentation style checks: isort, pycodestyle, pydocstyle
	@ruff check $(PY_PROJECT_ROOT) && \
		echo "🏆 Code/Doc style good to go!" || \
		(echo "💔 Please resolve all style warnings to ensure readability, scalability, and maintainability:\nruff check --fix $(PY_PROJECT_ROOT)"; exit 1)
	@echo Running code quality checks: pylint
	@pylint $(PY_PROJECT_NAME) $(PYLINT_EXTRAS) && \
		echo "🏆 Code quality good to go!" || \
		(echo "💔 Please resolve all code quality warnings to ensure scalability and maintainability."; exit 1)

# Check typehints for static typing best practices.
.PHONY: typing
typing:
	@echo Running code typechecks: mypy
	@mypy $(PY_PROJECT_ROOT) && \
		echo "🏆 Code typechecks good to go!" || \
		(echo "💔 Please resolve all typecheck warnings to ensure readability and stability."; exit 1)

# Check for common security issues/best practices.
.PHONY: security
security:
	@echo Running security scans: bandit
	@bandit -r -c=$(PY_PROJECT_ROOT)pyproject.toml $(PY_PROJECT_ROOT) && \
		echo "🏆 Code security good to go!" || \
		(echo "💔 Please resolve all security warnings to ensure user and developer safety."; exit 1)

# Check full code quality suite (minus unit tests) against source.
# Does not enforce unit tests to simplify pushes, unit tests should be automated via pipelines with standardized env.
# Ensure format is first, as it will often solve many style and lint failures.
.PHONY: qa
qa: format lint typing security

# Run basic unit tests.
.PHONY: test
test:
	@pytest $(PY_PROJECT_ROOT) --cov --cov-report="" && \
		echo "🏆 Tests good to go!" || \
		(echo "💔 Please resolve all test failures to ensure stability and quality."; exit 1)

##### Builds #####

# Package the library into a pip installable.
.PHONY: wheel
wheel:
	@python -m build && \
		echo "🏆 Wheel built successfully!" || \
		(echo "💔 Wheel build failed, resolve errors and try again."; exit 1)

# Perform a fully isolated build from the latest commit in a repository suite for release.
.PHONY: release
release:
	@curl https://raw.githubusercontent.com/pyranha-labs/build-tools/refs/heads/main/build_python_release.sh -o build_python_release.sh
	@chmod 755 build_python_release.sh
	@./build_python_release.sh && \
		echo "🏆 Release built successfully!" || \
		(echo "💔 Release build failed, resolve errors and try again."; exit 1)

# Clean the packages from all builds.
.PHONY: clean
clean:
	-rm -r $(PY_PROJECT_ROOT)dist $(PY_PROJECT_ROOT)$(PY_PROJECT_NAME).egg-info
