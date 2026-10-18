# Shared recipes for working on Python projects.

PY_MAKE_REPO := git@github.com:pyranha-labs/build-tools.git
PY_MAKE_REF := main
PY_MAKE_MAX_AGE := 86400  # Seconds before the shared recipes are pulled again, defaulting to daily pull.
PY_PROJECT_NAME := $(shell sed -n 's/^name = "\(.*\)"$$/\1/p' pyproject.toml)
# The project name as an import and how setuptools writes it: with underscores for hyphens.
PY_PACKAGE_NAME := $(subst -,_,$(PY_PROJECT_NAME))
PY_PROJECT_ROOT := $(dir $(realpath $(lastword $(MAKEFILE_LIST))))
# Named as project root so all documents are read rather than only those `[tool.pyqa] include` covers.
PY_LINKS_ROOT = $(PY_PROJECT_ROOT)

# Scope security/bandit to the checkout it runs from. Its exclusions are matched as substrings of the full path,
# so the absolute root `python.mk` computes drags the checkout's own location in: inside a worktree swallows the
# worktree's own source and the gate passes on zero lines. The suppressions check reads the same root, past
# `[tool.pyqa] include`, so every `# nosec` bandit honors is held to a justification.
PY_SECURITY_ROOT := ./

# Scope typing/mypy and lint/pylint to the package, since neither reads a target of its own without `files` set.
# Override as empty in a Makefile to read their `files` from `pyproject.toml` instead.
PY_SRC_ROOT := src/$(PY_PACKAGE_NAME)

# Run the full local gate. Pushes only enforce qa; see hooks/pre-push.
.PHONY: default
default: qa test

##### Shared Callables #####

PY_PYQA = uv run python $(PY_PROJECT_ROOT)tools/pyqa.py
PY_SINCE_FLAG = $(if $(since),--since $(since))

# Run a recipe's command, or only print it when `dry=true` is set, e.g. `make qa dry=true`.
# Given a pass and a fail message, it prints the one matching how the command ends, and a failure stops the recipe.
# Make splits a call's arguments at every comma, so a message writes one as `$(,)`.
, := ,
define PY_RUN
$(if $(filter true,$(dry)),\
	{ echo '$(subst ','\'',$(1))'; exit 0; },\
	$(1)$(if $(2), && echo "🏆 $(strip $(2))" || (echo "💔 $(strip $(3))"; exit 1)))
endef

# Narrow a run to the modules changed since a commit count or a git reference, e.g. `make qa since=origin/HEAD`.
# The pyqa recipes take the flag. Every other tool is handed the modules `pyqa.py --list` prints and applies its
# own exclusions, and runs nothing on an empty list, since a tool named no file reads its whole scope instead.
define PY_RUN_SINCE
$(call PY_RUN,$(if $(since),files="$$($(PY_PYQA) --list --since $(since))" && \
	if [ -n "$$files" ]; then $(1) $$files; \
	else echo "No python files changed since $(since)."; fi,$(1)),$(2),$(3))
endef

##### Shared Setups and Configurations #####

# Pull the shared quality files and docs from upstream, read-only so local edits are not lost to a pull.
# The repository can be internal, so the pull uses git over SSH; raw file URLs refuse an unauthenticated request.
define PY_MAKE_PULL
tmp_dir="$$(mktemp -d)" && trap 'rm -rf "$$tmp_dir"' EXIT && \
	git clone --quiet --depth 1 --no-checkout --single-branch --branch $(PY_MAKE_REF) $(PY_MAKE_REPO) "$$tmp_dir" && \
	git -C "$$tmp_dir" show HEAD:python.mk > "$$tmp_dir/python.mk" && \
	git -C "$$tmp_dir" show HEAD:tools/pyqa.py > "$$tmp_dir/pyqa.py" && \
	git -C "$$tmp_dir" show HEAD:tools/build_python_release.sh > "$$tmp_dir/build_python_release.sh" && \
	mkdir -p tools && \
	install -m 444 "$$tmp_dir/python.mk" python.mk && \
	install -m 555 "$$tmp_dir/pyqa.py" tools/pyqa.py && \
	install -m 555 "$$tmp_dir/build_python_release.sh" tools/build_python_release.sh
endef

# Keep the shared python recipes current: make remakes an included makefile before reading it, and restarts on a pull.
# A fresh copy is left untouched, so make does not restart. A failed pull keeps the existing copy to allow offline work.
# An empty PY_MAKE_REPO skips the pull, for the upstream repository itself.
.PHONY: FORCE
FORCE:
python.mk: FORCE
	@if [ -n "$(PY_MAKE_REPO)" ] && \
		{ [ ! -f tools/pyqa.py ] || [ $$(( $$(date +%s) - $$(date -r $@ +%s) )) -gt $(PY_MAKE_MAX_AGE) ]; }; then \
		($(PY_MAKE_PULL)) && echo "🏆 Shared python utilities pulled from: $(PY_MAKE_REPO)" || \
			echo "💔 Shared python utilities could not be pulled, continuing with the existing copy."; \
	fi

# Update the shared python recipes (this file) outside initial setup.
.PHONY: update-python-mk
update-python-mk:
	@($(PY_MAKE_PULL)) && echo "🏆 Shared python utilities pulled from: $(PY_MAKE_REPO)" || \
		(echo "💔 Shared python utilities could not be pulled, check SSH access to: $(PY_MAKE_REPO)"; exit 1)

##### Development Setups and Configurations #####

# Set up initial environment for development.
.PHONY: setup
setup:
	$(call PY_RUN,ln -sfnv ../../tools/pre-push .git/hooks/pre-push)
	$(call PY_RUN,-git remote add upstream $(UPSTREAM))
	$(call PY_RUN,-git fetch upstream)
	@echo "🏆 Git set up complete!"
	$(MAKE) clean-venv venv default
	@echo "🏆 Full set up complete!"

# Create python virtual environment for development/testing.
CMD_VENV = ln -sfnv $(PY_PROJECT_ROOT).venv/bin/activate $(PY_PROJECT_ROOT)activate && uv sync $(VENV_EXTRAS) && uv pip check
.PHONY: venv
venv:
	@$(call PY_RUN,$(CMD_VENV),\
		Virtual environment built successfully!,\
		Virtual environment set up failed$(,) resolve errors and try again.)

# Clean the python virtual environment.
CMD_CLEAN_VENV = -rm -r $(PY_PROJECT_ROOT)activate $(PY_PROJECT_ROOT).venv $(CLEAN_VENV_EXTRAS)
.PHONY: clean-venv
clean-venv:
	$(call PY_RUN,$(CMD_CLEAN_VENV))

##### Quality Assurance #####

# Check the installed dependency tree against the published advisories. Deliberately not appended to `qa`: this check
# needs the network, and `pre-push` hooks runs `qa` on every push, including from a machine that is offline or behind
# a proxy. Run it when a requirement moves, and on a schedule, rather than on every push.
CMD_AUDIT = uv run pip-audit $(AUDIT_EXTRAS)
.PHONY: audit
audit:
	@echo Running dependency vulnerability scan:
	@$(call PY_RUN,$(CMD_AUDIT),\
		Dependencies good to go!,\
		Please resolve all advisories$(,) by upgrading the requirement or recording why it does not apply.)

# Run the unit tests with a per-line coverage report, for finding the gaps `make test` only totals.
CMD_COVERAGE = uv run pytest --cov --cov-report=term-missing $(COVERAGE_EXTRAS)
.PHONY: coverage
coverage:
	@$(call PY_RUN,$(CMD_COVERAGE))

# Check that no debugging call is left in, and that every exemption kept for one still exempts something.
CMD_DEBUGGING = $(PY_PYQA) --debugging $(PY_SINCE_FLAG) $(DEBUGGING_EXTRAS)
.PHONY: debugging
debugging:
	@echo Running debugging call checks:
	@$(call PY_RUN,$(CMD_DEBUGGING),\
		Debugging calls good to go!,\
		Please remove debugging calls and cut stale ones.)

# Check that public, private, and property docstrings match expected definition shapes.
CMD_DOCSTRINGS = $(PY_PYQA) --docs $(PY_SINCE_FLAG) $(DOCSTRINGS_EXTRAS)
.PHONY: docstrings
docstrings:
	@echo Running docstring checks:
	@$(call PY_RUN,$(CMD_DOCSTRINGS),\
		Docstrings good to go!,\
		Please fold a private docstring's sections into its summary$(,) state the ones a public one asks for$(,) and keep summary lines to one sentence.)

# Check source code format for consistent patterns.
CMD_FORMAT = uv run ruff format --check --diff --force-exclude $(FORMAT_EXTRAS)
.PHONY: format
format:
	@echo Running code format checks:
	@$(call PY_RUN_SINCE,$(CMD_FORMAT),\
		Code format good to go!,\
		Please run 'make format-fix' to ensure code consistency and quality.)

# Rewrite source code into the format `make format` checks for.
CMD_FORMAT_FIX = uv run ruff format --force-exclude $(FORMAT_FIX_EXTRAS)
.PHONY: format-fix
format-fix:
	@echo Running code formatter:
	@$(call PY_RUN_SINCE,$(CMD_FORMAT_FIX),\
		Code format fixed!,\
		Please resolve the formatter errors above$(,) then run 'make format-fix' again.)

# Check that every relative link between documents resolves to a file and to a heading inside it.
CMD_LINKS = $(PY_PYQA) --links $(PY_LINKS_ROOT) $(LINKS_EXTRAS)
.PHONY: links
links:
	@echo Running documentation link checks:
	@$(call PY_RUN,$(CMD_LINKS),\
		Documentation links good to go!,\
		Please repoint a link whose file or heading moved.)

# Check for common lint/complexity/style issues.
# Ruff is used for isort, pycodestyle, pydocstyle. Pylint is used separately for greater coverage.
CMD_LINT = lint-ruff lint-pylint $(LINT_EXTRAS) # Run ruff before pylint since ruff is faster and can prevent many pylint failures.
CMD_LINT_RUFF = uv run ruff check --force-exclude $(LINT_RUFF_EXTRAS)
CMD_LINT_PYLINT = uv run pylint $(if $(since),,$(PY_SRC_ROOT)) $(LINT_PYLINT_EXTRAS)
.PHONY: lint lint-pylint lint-ruff
lint:
	@$(MAKE) $(CMD_LINT)
lint-pylint:
	@echo Running code quality checks:
	@$(call PY_RUN_SINCE,$(CMD_LINT_PYLINT),\
		Code quality good to go!,\
		Please resolve all code quality warnings to ensure scalability and maintainability.)
lint-ruff:
	@echo Running code and documentation style checks:
	@$(call PY_RUN_SINCE,$(CMD_LINT_RUFF),\
		Code/Documentation style good to go!,\
		Please resolve all style warnings to ensure readability$(,) scalability$(,) and maintainability:\nmake lint-fix)

# Rewrite source code into the format `make lint` checks for.
CMD_LINT_FIX = uv run ruff check --fix --force-exclude $(LINT_FIX_EXTRAS)
.PHONY: lint-fix
lint-fix:
	@echo Running code quality fixer:
	@$(call PY_RUN_SINCE,$(CMD_LINT_FIX),\
		Code/Documentation violations fixed!,\
		Please resolve the code/documentation fix failures above$(,) then run 'make lint-fix' again.)

# Check that every module declares items in the most consistent order.
CMD_ORDER = $(PY_PYQA) --order $(PY_SINCE_FLAG) $(ORDER_EXTRAS)
.PHONY: order
order:
	@echo Running definition order checks:
	@$(call PY_RUN,$(CMD_ORDER),\
		Definition order good to go!,\
		Please sort definitions so a reader can find them by name; 'make order-fix' sorts most.)

# Sort definitions into the order `make order` checks for.
CMD_ORDER_FIX = $(PY_PYQA) --order --fix $(PY_SINCE_FLAG) $(ORDER_FIX_EXTRAS)
.PHONY: order-fix
order-fix:
	@echo Running definition order fixes:
	@$(call PY_RUN,$(CMD_ORDER_FIX),\
		Definition order fixed!,\
		Please resolve the errors above$(,) then run 'make order-fix' again.)

# Check for common security issues/best practices.
CMD_SECURITY = uv run bandit -f txt -r -c=pyproject.toml $(if $(since),,$(PY_SECURITY_ROOT)) $(SECURITY_EXTRAS)
CMD_SECURITY_SUPPRESSIONS = $(PY_PYQA) --suppressions $(PY_SINCE_FLAG) $(PY_SECURITY_ROOT) $(SECURITY_SUPPRESSIONS_EXTRAS)
.PHONY: security
security:
	@echo Running security scans:
	@$(call PY_RUN_SINCE,$(CMD_SECURITY_SUPPRESSIONS) && $(CMD_SECURITY),\
		Code security good to go!,\
		Please resolve all security warnings to ensure user and developer safety.)

# Check full code quality suite (minus unit tests) against source. Does not enforce unit tests to simplify pushes,
# unit tests should be automated via pipelines with standardized env. Ensure format is before lint, as it will often
# solve many style and lint failures.
CMD_QA = $(MAKE) -k debugging docstrings order format lint typing security links $(QA_EXTRAS)
.PHONY: qa
qa:
	@$(CMD_QA)

# Run basic unit tests.
CMD_TEST = uv run pytest --cov --cov-report="" $(TEST_EXTRAS)
.PHONY: test
test:
	@echo Running unit tests:
	@$(call PY_RUN,$(CMD_TEST),\
		Tests good to go!,\
		Please resolve all test failures to ensure stability and quality.)

# Check typehints for static typing best practices.
CMD_TYPING = uv run mypy $(if $(since),,$(PY_SRC_ROOT)) $(TYPING_EXTRAS)
.PHONY: typing
typing:
	@echo Running code typechecks:
	@$(call PY_RUN_SINCE,$(CMD_TYPING),\
		Code typechecks good to go!,\
		Please resolve all typecheck warnings to ensure readability and stability.)

##### Builds #####

# Package the library into a pip installable.
CMD_WHEEL = uv build --wheel $(WHEEL_EXTRAS)
.PHONY: wheel
wheel:
	@$(call PY_RUN,$(CMD_WHEEL),\
		Wheel built successfully!,\
		Wheel build failed$(,) resolve errors and try again.)

# Perform a fully isolated build from the latest commit in a repository suite for release.
CMD_RELEASE = ./tools/build_python_release.sh $(RELEASE_EXTRAS)
.PHONY: release
release:
	@$(call PY_RUN,$(CMD_RELEASE),\
		Release built successfully!,\
		Release build failed$(,) resolve errors and try again.)

# Clean the packages from all builds. Setuptools writes the egg-info beside the package, flat or under `src/`.
CMD_CLEAN = -rm -rf $(PY_PROJECT_ROOT)dist \
	$(PY_PROJECT_ROOT)$(PY_PACKAGE_NAME).egg-info \
	$(PY_PROJECT_ROOT)src/$(PY_PACKAGE_NAME).egg-info \
	$(CLEAN_EXTRAS)
.PHONY: clean
clean:
	$(call PY_RUN,$(CMD_CLEAN))
