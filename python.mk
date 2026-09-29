# Shared recipes for working on Python projects.

PY_MAKE_ORIGIN := https://raw.githubusercontent.com/pyranha-labs/build-tools/refs/heads/main
PY_PROJECT_NAME := $(shell sed -n 's/^name = "\(.*\)"$$/\1/p' pyproject.toml)
PY_PROJECT_ROOT := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
PY_SRC_ROOT := src/$(PY_PROJECT_NAME)
PYLINT_EXTRAS :=
# Scope security/bandit to the checkout it runs from. Its exclusions are matched as substrings of the full path,
# so the absolute root `python.mk` computes drags the checkout's own location in: inside a worktree swallows the
# worktree's own source and the gate passes on zero lines.
PY_SECURITY_ROOT := ./

# Run the full local gate. Pushes only enforce qa; see hooks/pre-push.
.PHONY: default
default: qa test

##### Development Setups and Configurations #####

# Update the shared python recipes (this file) outside initial setup.
.PHONY: update-py-make
update-py-make:
	mkdir -v tools
	curl $(PY_MAKE_ORIGIN)/python.mk -o python.mk
	curl $(PY_MAKE_ORIGIN)/tools/build_python_release.sh -o tools/build_python_release.sh
	chmod 755 tools/build_python_release.sh
	curl $(PY_MAKE_ORIGIN)/tools/pyqa.py -o tools/pyqa.py
	chmod 755 tools/pyqa.py

# Create python virtual environment for development/testing.
.PHONY: venv
venv:
	@uv venv && uv sync && uv pip check && \
		ln -sfnv $(PY_PROJECT_ROOT).venv/bin/activate $(PY_PROJECT_ROOT)activate && \
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
	@uv run ruff format --check --diff $(PY_PROJECT_ROOT) && \
		echo "🏆 Code format good to go!" || \
		(echo "💔 Please run formatter to ensure code consistency and quality:\nuv run ruff format $(PY_PROJECT_ROOT)"; exit 1)

# Check that public, private, and property docstrings match expected definition shapes.
.PHONY: docstrings
docstrings:
	@echo Running docstring checks: pyqa --docs
	@uv run python $(PY_PROJECT_ROOT)tools/pyqa.py --docs $(PY_SRC_ROOT) $(PYLINT_EXTRAS) && \
		echo "🏆 Docstrings good to go!" || \
		(echo "💔 Please fold a private docstring's sections into its summary, state and spell the ones a public one asks for, and cut a summary line to one sentence."; exit 1)

# Check that every module declares items in the most consistent order.
.PHONY: order
order:
	@echo Running definition order checks: pyqa --order
	@uv run python $(PY_PROJECT_ROOT)tools/pyqa.py --order $(PY_SRC_ROOT) $(PYLINT_EXTRAS) && \
		echo "🏆 Definition order good to go!" || \
		(echo "💔 Please sort definitions so a reader can find one by name:\npython tools/pyqa.py --order --fix"; exit 1)

# Check for common lint/complexity/style issues.
# Ruff is used for isort, pycodestyle, pydocstyle. Pylint is used separately for greater coverage.
.PHONY: lint
lint:
	@echo Running code and documentation style checks: isort, pycodestyle, pydocstyle
	@uv run ruff check $(PY_PROJECT_ROOT) && \
		echo "🏆 Code/Doc style good to go!" || \
		(echo "💔 Please resolve all style warnings to ensure readability, scalability, and maintainability:\nuv run ruff check --fix $(PY_PROJECT_ROOT)"; exit 1)
	@echo Running code quality checks: pylint
	@uv run pylint $(PY_SRC_ROOT) $(PYLINT_EXTRAS) && \
		echo "🏆 Code quality good to go!" || \
		(echo "💔 Please resolve all code quality warnings to ensure scalability and maintainability."; exit 1)

# Check typehints for static typing best practices.
.PHONY: typing
typing:
	@echo Running code typechecks: mypy
	@uv run mypy $(PY_PROJECT_ROOT) && \
		echo "🏆 Code typechecks good to go!" || \
		(echo "💔 Please resolve all typecheck warnings to ensure readability and stability."; exit 1)

# Check for common security issues/best practices.
.PHONY: security
security:
	@echo Running security scans: bandit
	@uv run bandit -r -c=$(PY_SECURITY_ROOT)pyproject.toml $(PY_SECURITY_ROOT) && \
		echo "🏆 Code security good to go!" || \
		(echo "💔 Please resolve all security warnings to ensure user and developer safety."; exit 1)

# Check that every relative link between documents resolves to a file and to a heading inside it.
.PHONY: links
links:
	@echo Running documentation link checks: pyqa --links
	@uv run python $(PY_PROJECT_ROOT)tools/pyqa.py --links && \
		echo "🏆 Documentation links good to go!" || \
		(echo "💔 Please repoint a link whose file or heading moved."; exit 1)

# Check full code quality suite (minus unit tests) against source.
# Does not enforce unit tests to simplify pushes, unit tests should be automated via pipelines with standardized env.
# Ensure format is first, as it will often solve many style and lint failures.
.PHONY: qa
qa: format docstrings order lint typing security links

# Run basic unit tests.
.PHONY: test
test:
	@uv run pytest $(PY_PROJECT_ROOT) --cov --cov-report="" && \
		echo "🏆 Tests good to go!" || \
		(echo "💔 Please resolve all test failures to ensure stability and quality."; exit 1)

# Run the unit tests with a per-line coverage report, for finding the gaps `make test` only totals.
.PHONY: coverage
coverage:
	@uv run pytest $(PY_PROJECT_ROOT) --cov --cov-report=term-missing

# Check the installed dependency tree against the published advisories.
# Deliberately not appended to `qa`: this check needs the network, and `hooks/pre-push` runs `qa` on every push,
# including from a machine that is offline or behind a proxy. Run it when a requirement moves, and on a schedule,
# rather than on every push.
.PHONY: audit
audit:
	@echo Running dependency vulnerability scan: pip-audit
	@uv run pip-audit && \
		echo "🏆 Dependencies good to go!" || \
		(echo "💔 Please resolve all advisories, by upgrading the requirement or recording why it does not apply."; exit 1)

##### Builds #####

# Package the library into a pip installable.
.PHONY: wheel
wheel:
	@uv build --wheel && \
		echo "🏆 Wheel built successfully!" || \
		(echo "💔 Wheel build failed, resolve errors and try again."; exit 1)

# Perform a fully isolated build from the latest commit in a repository suite for release.
.PHONY: release
release:
	@./tools/build_python_release.sh && \
		echo "🏆 Release built successfully!" || \
		(echo "💔 Release build failed, resolve errors and try again."; exit 1)

# Clean the packages from all builds.
.PHONY: clean
clean:
	-rm -r $(PY_PROJECT_ROOT)dist $(PY_PROJECT_ROOT)$(PY_PROJECT_NAME).egg-info
