# Additional recipes for Python based development.
-include python.mk

##### Project Overrides #####

PYLINT_EXTRAS := <REMOVE OR REPLACE WITH EXTRA FILES/FOLDERS>

##### Initial Development Setups and Configurations #####

UPSTREAM := <REPLACE WITH GITHUB PROJECT ORG/REPO PATH; e.g., git@github.com:myorg/myproject.git>

# Set up initial environment for development.
.PHONY: setup
setup:
	ln -sfnv $(PY_PROJECT_ROOT)tools/pre-push $(PY_PROJECT_ROOT).git/hooks/pre-push
	-git remote add upstream $(UPSTREAM)
	-git fetch upstream
	@echo "🏆 Git set up complete!"
	curl -fsSL https://raw.githubusercontent.com/pyranha-labs/build-tools/refs/heads/main/python.mk -o python.mk
	make update-python-mk clean-venv venv default
	@echo "🏆 Full set up complete!"
