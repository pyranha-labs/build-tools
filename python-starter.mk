# Additional recipes for Python based development.
-include python.mk

##### Project Overrides #####

PYLINT_EXTRAS := <REMOVE OR REPLACE WITH EXTRA FILES/FOLDERS>

##### Initial Development Setups and Configurations #####

UPSTREAM := git@github.com:<REPLACE WITH GITHUB PROJECT ORG/REPO PATH>.get

# Set up initial environment for development.
.PHONY: setup
setup:
	ln -sfnv $(PY_PROJECT_ROOT).hooks/pre-push $(PY_PROJECT_ROOT).git/hooks/pre-push
	-git remote add upstream $(UPSTREAM)
	-git fetch upstream
	@echo "🏆 Git set up complete!"
	curl https://raw.githubusercontent.com/pyranha-labs/build-tools/refs/heads/main/python.mk -o python.mk
	make clean-venv venv
	. $(PY_PROJECT_ROOT)activate && make qa test
