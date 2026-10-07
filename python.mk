# Shared recipes for working on Python projects.

PY_MAKE_REPO := git@github.com:pyranha-labs/build-tools.git
PY_MAKE_REF := main
PY_MAKE_MAX_AGE := 86400  # Seconds before the shared recipes are pulled again, defaulting to daily pull.
PY_PROJECT_NAME := $(shell sed -n 's/^name = "\(.*\)"$$/\1/p' pyproject.toml)
PY_PROJECT_ROOT := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
PY_SRC_ROOT := src/$(PY_PROJECT_NAME)
PYLINT_EXTRAS :=
BANDIT_EXTRAS :=
# Scope security/bandit to the checkout it runs from. Its exclusions are matched as substrings of the full path,
# so the absolute root `python.mk` computes drags the checkout's own location in: inside a worktree swallows the
# worktree's own source and the gate passes on zero lines.
PY_SECURITY_ROOT := ./

# Run the full local gate. Pushes only enforce qa; see hooks/pre-push.
.PHONY: default
default: qa test

##### Development Setups and Configurations #####

# Pull the shared quality files from upstream, read-only so local edits are not lost to a pull.
# The repository can be internal, so the pull uses git over SSH; raw file URLs refuse an unauthenticated request.
define PY_MAKE_PULL
tmp_dir="$$(mktemp -d)" && trap 'rm -r "$$tmp_dir"' EXIT && \
	git clone --quiet --depth 1 --no-checkout --single-branch --branch $(PY_MAKE_REF) $(PY_MAKE_REPO) "$$tmp_dir" && \
	git -C "$$tmp_dir" show HEAD:python.mk > "$$tmp_dir/python.mk" && \
	git -C "$$tmp_dir" show HEAD:tools/build_python_release.sh > "$$tmp_dir/build_python_release.sh" && \
	git -C "$$tmp_dir" show HEAD:tools/pyqa.py > "$$tmp_dir/pyqa.py" && \
	mkdir -p tools && \
	install -m 444 "$$tmp_dir/python.mk" python.mk && \
	install -m 555 "$$tmp_dir/build_python_release.sh" tools/build_python_release.sh && \
	install -m 555 "$$tmp_dir/pyqa.py" tools/pyqa.py
endef

# Keep the shared python recipes current: make remakes an included makefile before reading it, and restarts on a pull.
# A fresh copy is left untouched, so make does not restart. A failed pull keeps the existing copy to allow offline work.
python.mk: FORCE
	@if [ ! -f tools/pyqa.py ] || [ $$(( $$(date +%s) - $$(date -r $@ +%s) )) -gt $(PY_MAKE_MAX_AGE) ]; then \
		($(PY_MAKE_PULL)) && echo "🏆 Shared python utilities pulled from: $(PY_MAKE_REPO)" || \
			echo "💔 Shared python utilities could not be pulled, continuing with the existing copy."; \
	fi

.PHONY: FORCE
FORCE:

# Update the shared python recipes (this file) outside initial setup.
.PHONY: update-python-mk
update-python-mk:
	@($(PY_MAKE_PULL)) && \
		echo "🏆 Shared python utilities pulled from: $(PY_MAKE_REPO)" || \
		(echo "💔 Shared python utilities could not be pulled, check SSH access to: $(PY_MAKE_REPO)"; exit 1)

# Create python virtual environment for development/testing.
.PHONY: venv
venv:
	@uv sync && uv pip check && \
		ln -sfnv $(PY_PROJECT_ROOT).venv/bin/activate $(PY_PROJECT_ROOT)activate && \
		echo "🏆 Virtual environment built successfully!" || \
		(echo "💔 Virtual environment set up failed, resolve errors and try again."; exit 1)

# Clean the python virtual environment.
.PHONY: clean-venv
clean-venv:
	-rm -r $(PY_PROJECT_ROOT)activate $(PY_PROJECT_ROOT).venv

##### Quality Assurance #####

# Check the installed dependency tree against the published advisories. Deliberately not appended to `qa`: this check
# needs the network, and `pre-push` hooks runs`qa` on every push, including from a machine that is offline or behind
# a proxy. Run it when a requirement moves, and on a schedule, rather than on every push.
.PHONY: audit
audit:
	@echo Running dependency vulnerability scan: pip-audit
	@uv run pip-audit && \
		echo "🏆 Dependencies good to go!" || \
		(echo "💔 Please resolve all advisories, by upgrading the requirement or recording why it does not apply."; exit 1)

# Run the unit tests with a per-line coverage report, for finding the gaps `make test` only totals.
.PHONY: coverage
coverage:
	@uv run pytest $(PY_PROJECT_ROOT) --cov --cov-report=term-missing

# Check that no debugging call is left in, and that every exemption kept for one still exempts something.
.PHONY: debugging
debugging:
	@echo Running debugging call checks: pyqa --debugging
	@uv run python $(PY_PROJECT_ROOT)tools/pyqa.py --debugging $(PY_SRC_ROOT) $(PYLINT_EXTRAS) && \
		echo "🏆 Debugging calls good to go!" || \
		(echo "💔 Please remove debugging calls and cut stale ones."; exit 1)

# Check that public, private, and property docstrings match expected definition shapes.
.PHONY: docstrings
docstrings:
	@echo Running docstring checks: pyqa --docs
	@uv run python $(PY_PROJECT_ROOT)tools/pyqa.py --docs $(PY_SRC_ROOT) $(PYLINT_EXTRAS) && \
		echo "🏆 Docstrings good to go!" || \
		(echo "💔 Please fold a private docstring's sections into its summary, state the ones a public one asks for, and keep summary lines to one sentence."; exit 1)

# Check source code format for consistent patterns.
.PHONY: format
format:
	@echo Running code format checks: black/ruff
	@uv run ruff format --check --diff $(PY_PROJECT_ROOT) && \
		echo "🏆 Code format good to go!" || \
		(echo "💔 Please run 'make format-fix' to ensure code consistency and quality."; exit 1)

# Rewrite source code into the format `make format` checks for.
.PHONY: format-fix
format-fix:
	@echo Running code formatter: ruff
	@uv run ruff format $(PY_PROJECT_ROOT) && \
		echo "🏆 Code format fixed!" || \
		(echo "💔 Please resolve the formatter errors above, then run 'make format-fix' again."; exit 1)

# Check that every relative link between documents resolves to a file and to a heading inside it.
.PHONY: links
links:
	@echo Running documentation link checks: pyqa --links
	@uv run python $(PY_PROJECT_ROOT)tools/pyqa.py --links && \
		echo "🏆 Documentation links good to go!" || \
		(echo "💔 Please repoint a link whose file or heading moved."; exit 1)

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

# Check that every module declares items in the most consistent order.
.PHONY: order
order:
	@echo Running definition order checks: pyqa --order
	@uv run python $(PY_PROJECT_ROOT)tools/pyqa.py --order $(PY_SRC_ROOT) $(PYLINT_EXTRAS) && \
		echo "🏆 Definition order good to go!" || \
		(echo "💔 Please sort definitions so a reader can find them by name; 'make order-fix' sorts most."; exit 1)

# Sort definitions into the order `make order` checks for.
.PHONY: order-fix
order-fix:
	@echo Running definition order fixes: pyqa --order --fix
	@uv run python $(PY_PROJECT_ROOT)tools/pyqa.py --order --fix $(PY_SRC_ROOT) $(PYLINT_EXTRAS) && \
		echo "🏆 Definition order fixed!" || \
		(echo "💔 Please resolve the errors above, then run 'make order-fix' again."; exit 1)

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
	@uv run bandit -r -c=pyproject.toml $(PY_SECURITY_ROOT) $(BANDIT_EXTRAS) && \
		echo "🏆 Code security good to go!" || \
		(echo "💔 Please resolve all security warnings to ensure user and developer safety."; exit 1)

# Check full code quality suite (minus unit tests) against source. Does not enforce unit tests to simplify pushes,
# unit tests should be automated via pipelines with standardized env. Ensure format is before lint, as it will often
# solve many style and lint failures.
.PHONY: qa
qa: debugging docstrings order format lint typing security links

# Run basic unit tests.
.PHONY: test
test:
	@uv run pytest $(PY_PROJECT_ROOT) --cov --cov-report="" && \
		echo "🏆 Tests good to go!" || \
		(echo "💔 Please resolve all test failures to ensure stability and quality."; exit 1)

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
