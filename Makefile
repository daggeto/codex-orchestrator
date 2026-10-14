.DEFAULT_GOAL := help

PYTHON ?= python3
export ARGS ORCH_ARGS ORCH NPM

# The catalog supplies both the target names and their documentation.
COMMANDS := $(shell $(PYTHON) scripts/commands.py --targets)
HELP_REQUESTED := $(filter help,$(MAKECMDGOALS))

.PHONY: help $(COMMANDS)

help:
ifeq ($(filter-out help,$(MAKECMDGOALS)),)
	@$(PYTHON) scripts/commands.py help
else
	@:
endif

$(COMMANDS):
	@$(PYTHON) scripts/commands.py $@ $(if $(HELP_REQUESTED),--help-mode,)
