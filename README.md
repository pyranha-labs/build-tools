# Build Tools

Cross-project setup and build tools that guarantee code/process consistency and quality.

## Table Of Contents

* [How Tos](#how-tos)
    * [Bootstrap New Python Project](#bootstrap-new-python-project)
    * [Install Python Project Starter Makefile](#install-python-project-starter-makefile)
    * [Manually Install Python Common Makefile](#manually-install-python-common-makefile)
    * [Use Python Common Recipes](#use-python-common-recipes)
    * [Use Python Release Builder](#use-python-release-builder)
    * [Contribute](#contribute)

## How Tos

Installation and usage will vary based on the external project goal. The tools in this repository are designed to be
used as either:

- Starter templates to be copied into a project.
- Pulled on-demand by the template once placed into a project.

Either way, the tools will typically not be used directly from a full clone of this project. Example use case:

1. Copy the Python starter Makefile template from this project into a new project.
1. Update the placeholders.
1. Commit the completed template for future use.
1. Use recipes, such as `make setup` and `make wheel` to configure the Python project and build a Python wheel.

> Recipes rely on `uv` for easy python and virtual environment management, and more.
> For installation steps refer to: [Astral - uv](https://docs.astral.sh/uv/getting-started/installation/)

### Bootstrap New Python Project

1. Copy [python-starter-.gitignore](python-starter-.gitignore) to `.gitignore` in the new project.
1. Follow [Install Python Project Starter Makefile](#install-python-project-starter-makefile)

### Install Python Project Starter Makefile

1. Copy the [python-starter-Makefile.mk](python-starter-Makefile) template to `Makefile` in a new project.
1. Update the placeholders at the top of the file.
    - `UPSTREAM` is required and must be the project's upstream git repository path.
1. Run `make setup` to set up the project and clone the Python common recipes for future use.
1. Optional: Follow [Use Python Common Recipes](#use-python-common-recipes) as needed.

### Manually Install Python Common Makefile

The process is performed automatically by the Python project starter Makefile. To use the file manually:

1. Copy the [python.mk](./python.mk) to `python.mk` in an existing project.
1. Copy the [tools/pyqa.py](./tools/pyqa.py) to `tools/pyqa.py` in an existing project.
1. Add `-include python.mk` to the top of the primary `Makefile` in the project.
1. Follow [Use Python Common Recipes](#use-python-common-recipes) as needed.

### Use Python Common Recipes

> Recipes read the paths they check from `pyproject.toml` rather than from make variables, wherever the tool allows.
> - The `lint` and `typing` recipes run `pylint` and `mypy` on `PY_SRC_ROOT`, the `src/` package by default. Set it
>   empty in the project's `Makefile` to read their `files` from `pyproject.toml` instead.
> - The `security` recipe uses `bandit`, which reads no target from `pyproject.toml`, so `PY_SECURITY_ROOT` names it.

1. Run `make <recipe>` to perform a specific Python-based process. Example: `make test`
1. Optional: Add `<NAME>_EXTRAS` to pass extra arguments to the command `CMD_<NAME>` in `python.mk` runs, on the
   command line or in the project's `Makefile`. Example: `make typing TYPING_EXTRAS=--strict`
1. Optional: Add `since=<count or git ref>` to check only files changed since then. Example: `make qa since=origin/HEAD`
1. Optional: Add `dry=true` to print each recipe's commands instead of running them. Example: `make qa dry=true`

The Python common Makefile contains the following recipes:

- `venv` - Create a Python virtual environment for development/testing.
- `qa` - Check full code quality suite (minus unit tests) against source.
- `test` - Run basic unit tests.
- `audit` - Run lightweight package audit for vulnerabilities.
- `clean` - Remove the packages from previous builds.
- `clean-venv` - Remove the Python virtual environment.
- `debugging` - Check source code for debugging calls left in, and exemptions that exempt nothing.
- `docstrings` - Check source code docstrings for consistent declarations.
- `format` - Check source code format for consistent patterns.
- `format-fix` - Rewrite source code into the format `format` checks for.
- `lint` - Check for common lint/complexity/style issues.
- `lint-fix` - Rewrite source code into the format `lint` checks for.
- `order` - Check source code for consistent content order.
- `order-fix` - Sort source code into the order `order` checks for.
- `release` - Package the library into a pip installable.
- `security` - Check for common security issues/best practices.
- `typing` - Check typehints for static typing best practices.
- `wheel` - Package the library into a pip installable.

### Use Python Release Builder

The process is performed automatically by the `make release` shared python recipe. To use the file manually:

1. Copy the [build_python_release.sh](./tools/build_python_release.sh) to `build_python_release.sh` in an existing project.
1. Run `build_python_release.sh`.
1. Follow the steps printed to upload.

### Contribute

Refer to the [Contributing Guide](CONTRIBUTING.md) for information on how to contribute to this project.
