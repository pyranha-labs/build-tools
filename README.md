# Build Tools

Cross-project setup and build tools that guarantee code/process consistency and quality.


## Table Of Contents

  * [How Tos](#how-tos)
    * [Install Python Project Starter Makefile](#install-python-project-starter-makefile)
    * [Install Python Common Makefile](#install-python-common-makefile)
    * [Use Python Common Recipes](#use-python-common-recipes)
    * [Contribute](#contribute)


## How Tos

Installation and usage will vary based on the external project goal.
The tools in this repository are designed to be used as either:
- Starter templates to be copied into a project.
- Pulled on-demand by the template once placed into a project.

Either way, the tools will typically not be used directly from a full clone of this project. Example use case:
1. Copy the Python starter Makefile template from this project into a new project.
1. Update the placeholders.
1. Commit the completed template for future use.
1. Use recipes, such as `make setup` and `make wheel` to configure the Python project and build a Python wheel.

### Install Python Project Starter Makefile

1. Copy the [python-starter.mk](./python-starter.mk) template to `Makefile` in a new project.
1. Update the placeholders at the top of the file.
    - `UPSTREAM` is required, and must be the project's upstream git repository path.
    - `PYLINT_EXTRAS` is optional, and can be used to add additional file(s)/folder(s) to the lint jobs.
1. Run `make setup` to set up the project and clone the Python common recipes for future use.
1. Optional: Follow [Use Python Common Recipes](#use-python-common-makefile-recipes) as needed.

### Install Python Common Makefile

The process is performed automatically by the Python project starter Makefile.
To use the file manually:
1. Copy the [python.mk](./python.mk) to `python.mk` in an existing project.
1. Add `-include python.mk` to the top of the primary `Makefile` in the project.
1. Follow [Use Python Common Recipes](#use-python-common-makefile-recipes) as needed.

### Use Python Common Recipes

1. Run `make <recipe>` to perform a specific Python-based process. Example: `make test`.

The Python common Makefile contains the following recipes:
- `venv` - Create a Python virtual environment for development/testing.
- `clean-venv` - Remove the Python virtual environment.
- `format` - Check source code format for consistent patterns.
- `lint` - Check for common lint/complexity/style issues.
- `typing` - Check typehints for static typing best practices.
- `security` - Check for common security issues/best practices.
- `qa` - Check full code quality suite (minus unit tests) against source.
- `test` - Run basic unit tests.
- `wheel` - Package the library into a pip installable.
- `clean` - Remove the packages from previous builds.


### Contribute

Refer to the [Contributing Guide](CONTRIBUTING.md) for information on how to contribute to this project.
