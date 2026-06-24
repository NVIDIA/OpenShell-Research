UV ?= uv
PYTHON ?= python3

.PHONY: install install-backend install-sim sim smoke backend

install:
	$(UV) pip install -e ".[backend,sdk]"

install-backend:
	$(UV) pip install -e ".[backend]"

install-sim:
	$(UV) pip install -e ".[backend,sim]"

sim:
	reachy-mini-daemon --sim --scene $${REACHY_SCENE:-minimal}

smoke:
	$(PYTHON) -m reachy_openshell.smoke

backend:
	$(PYTHON) -m reachy_openshell.backend
