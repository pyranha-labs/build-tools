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
	@# Bootstrap python.mk once; from then on it pulls itself and other utilities whenever make runs.
	@[ -f python.mk ] || (tmp_dir="$$(mktemp -d)" && trap 'rm -r "$$tmp_dir"' EXIT && \
		git clone --quiet --depth 1 --no-checkout git@github.com:pyranha-labs/build-tools.git "$$tmp_dir" && \
		git -C "$$tmp_dir" show HEAD:python.mk > python.mk)
	$(MAKE) clean-venv venv default
	@echo "🏆 Full set up complete!"
