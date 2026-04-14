BACKEND_HOST ?= "127.0.0.1"
BACKEND_PORT ?= 3000
BACKEND_HOST_PORT = "$(BACKEND_HOST):$(BACKEND_PORT)"
FRONTEND_PORT = 3001
OPENHANDS_PATH ?= ".."
OPENHANDS := $(OPENHANDS_PATH)
OPENHANDS_FRONTEND_PATH = $(OPENHANDS)/frontend/build

# ANSI color codes
GREEN=$(shell tput -Txterm setaf 2)
YELLOW=$(shell tput -Txterm setaf 3)
RED=$(shell tput -Txterm setaf 1)
BLUE=$(shell tput -Txterm setaf 6)
RESET=$(shell tput -Txterm sgr0)

build:
	@poetry install
	@cd $(OPENHANDS) && $(MAKE) build


_run_setup:
	@echo "$(YELLOW)Starting backend server on port $(BACKEND_PORT)...$(RESET)"
	@FRONTEND_DIRECTORY=$(OPENHANDS_FRONTEND_PATH) poetry run uvicorn saas_server:app --host $(BACKEND_HOST) --port $(BACKEND_PORT) &
	@echo "$(YELLOW)Waiting for the backend to start...$(RESET)"
	@until nc -z localhost $(BACKEND_PORT); do sleep 0.1; done
	@echo "$(GREEN)Backend started successfully on port $(BACKEND_PORT).$(RESET)"

run:
	@echo "$(YELLOW)Running the app...$(RESET)"
	@$(MAKE) -s _run_setup
	@cd $(OPENHANDS) && $(MAKE) -s start-frontend
	@echo "$(GREEN)Application started successfully.$(RESET)"

# Start backend
start-backend:
	@echo "$(YELLOW)Starting backend on port $(BACKEND_PORT)...$(RESET)"
	@echo "$(OPENHANDS_FRONTEND_PATH)"
	@FRONTEND_DIRECTORY=$(OPENHANDS_FRONTEND_PATH) poetry run uvicorn saas_server:app --host $(BACKEND_HOST) --port $(BACKEND_PORT) --reload-dir $(OPENHANDS_PATH) --reload --reload-dir ./ --reload-exclude "./workspace"


lint:
	@poetry run pre-commit run --all-files --show-diff-on-failure --config ./dev_config/python/.pre-commit-config.yaml

# Start backend on port 3009 (for production with Apache reverse proxy)
# Note: Ensure frontend is built first with 'cd ../frontend && npm run build'
start-backend-3009:
	@echo "$(YELLOW)Starting Enterprise backend on port 3009...$(RESET)"
	@echo "$(YELLOW)Serving static frontend from: $(OPENHANDS_FRONTEND_PATH)$(RESET)"
	@BACKEND_PORT=3009 $(MAKE) start-backend
