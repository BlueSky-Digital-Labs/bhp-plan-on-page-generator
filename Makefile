# Horizon Digital Monorepo Makefile
# Manages both backend and frontend services across all environments

.PHONY: help build start stop restart logs shell clean install test lint format migrate collectstatic backup restore

# Default environment
ENV ?= development

# Docker Compose files
COMPOSE_FILE_DEV = docker-compose.yml -f docker-compose.dev.yml
COMPOSE_FILE_UAT = docker-compose.yml -f docker-compose.uat.yml
COMPOSE_FILE_PROD = docker-compose.yml -f docker-compose.prod.yml

# Select compose file based on environment
ifeq ($(ENV),development)
    COMPOSE_FILES = $(COMPOSE_FILE_DEV)
else ifeq ($(ENV),dev)
    COMPOSE_FILES = $(COMPOSE_FILE_DEV)
else ifeq ($(ENV),uat)
    COMPOSE_FILES = $(COMPOSE_FILE_UAT)
else ifeq ($(ENV),production)
    COMPOSE_FILES = $(COMPOSE_FILE_PROD)
else ifeq ($(ENV),prod)
    COMPOSE_FILES = $(COMPOSE_FILE_PROD)
else
    COMPOSE_FILES = docker-compose.yml
endif

# Always use single .env file
ENV_FILE = .env

# Colors for output
RED = \033[0;31m
GREEN = \033[0;32m
YELLOW = \033[0;33m
BLUE = \033[0;34m
NC = \033[0m # No Color

##@ Help
help: ## Display this help
	@echo "$(BLUE)Horizon Digital Monorepo Management$(NC)"
	@echo "=================================="
	@echo ""
	@echo "$(YELLOW)Usage:$(NC) make [target] [ENV=dev|uat|prod]"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

##@ Environment Management
setup: ## Initial setup for development
	@echo "$(GREEN)🚀 Setting up Horizon Digital development environment...$(NC)"
	@./env-switch.sh development
	@echo "$(GREEN)✅ Environment configured for development$(NC)"
	@echo "$(YELLOW)📝 Please review and update .env file with your specific values$(NC)"

env-check: ## Check environment variables
	@echo "$(BLUE)🔍 Checking environment: $(ENV)$(NC)"
	@if [ ! -f $(ENV_FILE) ]; then \
		echo "$(RED)❌ Environment file $(ENV_FILE) not found!$(NC)"; \
		echo "$(YELLOW)💡 Run './env-switch.sh $(ENV)' to create environment file$(NC)"; \
		exit 1; \
	fi
	@echo "$(GREEN)✅ Environment file $(ENV_FILE) exists$(NC)"

env-switch: ## Switch environment (usage: make env-switch ENV=development|uat|production)
	@echo "$(BLUE)🔄 Switching to $(ENV) environment...$(NC)"
	@./env-switch.sh $(ENV)
	@echo "$(GREEN)✅ Environment switched to $(ENV)$(NC)"

##@ Docker Operations
build: env-check ## Build all services
	@echo "$(GREEN)🏗️  Building services for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) build

start: env-check ## Start all services
	@echo "$(GREEN)🚀 Starting services for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) up -d
	@echo "$(GREEN)✅ Services started successfully!$(NC)"
	@make status

stop: ## Stop all services
	@echo "$(YELLOW)🛑 Stopping services for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) down

down: stop ## Alias for stop command

restart: ## Restart all services
	@echo "$(YELLOW)🔄 Restarting services for $(ENV) environment...$(NC)"
	@make stop ENV=$(ENV)
	@make start ENV=$(ENV)

status: ## Show service status
	@echo "$(BLUE)📊 Service status for $(ENV) environment:$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) ps

logs: ## Show logs for all services
	@echo "$(BLUE)📜 Showing logs for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) logs -f

logs-backend: ## Show backend logs
	@echo "$(BLUE)📜 Showing backend logs for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) logs -f backend

logs-frontend: ## Show frontend logs
	@echo "$(BLUE)📜 Showing frontend logs for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) logs -f frontend

logs-nginx: ## Show nginx logs
	@echo "$(BLUE)📜 Showing nginx logs for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) logs -f nginx

##@ Individual Service Management
start-backend: env-check ## Start only backend services
	@echo "$(GREEN)🐍 Starting backend services for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) up -d postgres redis backend celery_worker celery_beat

start-frontend: env-check ## Start only frontend service
	@echo "$(GREEN)⚛️  Starting frontend service for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) up -d frontend

start-nginx: env-check ## Start only nginx service
	@echo "$(GREEN)🌐 Starting nginx service for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) up -d nginx

##@ Database Operations
migrate: ## Run database migrations
	@echo "$(GREEN)🗄️  Running database migrations for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend python manage.py migrate

makemigrations: ## Create new database migrations
	@echo "$(GREEN)📝 Creating database migrations for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend python manage.py makemigrations

collectstatic: ## Collect static files
	@echo "$(GREEN)📁 Collecting static files for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend python manage.py collectstatic --noinput

createsuperuser: ## Create Django superuser
	@echo "$(GREEN)👤 Creating Django superuser for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend python manage.py createsuperuser

schema: ## Generate OpenAPI schema
	@echo "$(GREEN)📄 Generating OpenAPI schema for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend python manage.py spectacular --color --file schema.yml

docs: ## Show API documentation URLs
	@echo "$(BLUE)📖 API Documentation URLs for $(ENV) environment:$(NC)"
	@echo "  Swagger UI: http://localhost:8080/api/docs/"
	@echo "  ReDoc:      http://localhost:8080/api/redoc/"
	@echo "  Schema:     http://localhost:8080/api/schema/"

##@ Development Tools
shell: ## Access backend shell
	@echo "$(BLUE)🐚 Opening backend shell for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend python manage.py shell

dbshell: ## Open database shell
	@echo "$(BLUE)🗄️  Opening database shell for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend python manage.py dbshell

shell-backend: ## Access backend container bash
	@echo "$(BLUE)🐚 Opening backend container bash for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend bash

shell-frontend: ## Access frontend container bash
	@echo "$(BLUE)🐚 Opening frontend container bash for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec frontend sh

shell-nginx: ## Access nginx container bash
	@echo "$(BLUE)🐚 Opening nginx container bash for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec nginx sh

shell-postgres: ## Access postgres container bash
	@echo "$(BLUE)🐚 Opening postgres container bash for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec postgres bash

shell-redis: ## Access redis-cli in Redis container
	@echo "$(BLUE)🐚 Opening redis-cli for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec redis redis-cli

##@ Code Quality
install: ## Install dependencies for both backend and frontend
	@echo "$(GREEN)📦 Installing backend dependencies...$(NC)"
	@cd backend && make install
	@echo "$(GREEN)📦 Installing frontend dependencies...$(NC)"
	@cd frontend && make install

lint: ## Run linting for both backend and frontend
	@echo "$(GREEN)🔍 Running backend linting...$(NC)"
	@cd backend && make lint
	@echo "$(GREEN)🔍 Running frontend linting...$(NC)"
	@cd frontend && make lint

format: ## Format code for both backend and frontend
	@echo "$(GREEN)✨ Formatting backend code...$(NC)"
	@cd backend && make format
	@echo "$(GREEN)✨ Formatting frontend code...$(NC)"
	@cd frontend && make format

test: ## Run tests for both backend and frontend
	@echo "$(GREEN)🧪 Running backend tests...$(NC)"
	@cd backend && make test
	@echo "$(GREEN)🧪 Running frontend tests...$(NC)"
	@cd frontend && make test

test-backend: ## Run backend tests only
	@echo "$(GREEN)🧪 Running backend tests for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend python manage.py test

test-coverage: ## Run backend tests with coverage
	@echo "$(GREEN)🧪 Running backend tests with coverage for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend coverage run --source='.' manage.py test
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend coverage report

##@ Maintenance
clean: ## Clean up Docker resources
	@echo "$(YELLOW)🧹 Cleaning up Docker resources...$(NC)"
	docker system prune -f
	docker volume prune -f
	@echo "$(GREEN)✅ Cleanup completed$(NC)"

clean-all: ## Remove all containers, images, and volumes
	@echo "$(RED)⚠️  This will remove ALL Docker containers, images, and volumes!$(NC)"
	@read -p "Are you sure? (y/N): " confirm && [ "$$confirm" = "y" ] || exit 1
	docker-compose -f $(COMPOSE_FILES) down --volumes --remove-orphans
	docker system prune -a -f --volumes
	@echo "$(GREEN)✅ All Docker resources removed$(NC)"

update: ## Pull latest images and rebuild
	@echo "$(GREEN)📥 Pulling latest images and rebuilding...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) pull
	@make build ENV=$(ENV)
	@echo "$(GREEN)✅ Update completed$(NC)"

##@ SSL Certificates (Production)
ssl-setup: ## Setup SSL certificates (production only)
	@if [ "$(ENV)" != "prod" ]; then \
		echo "$(RED)❌ SSL setup is only available for production environment$(NC)"; \
		exit 1; \
	fi
	@echo "$(GREEN)🔒 Setting up SSL certificates...$(NC)"
	@mkdir -p nginx/ssl
	@echo "$(YELLOW)📝 Please place your SSL certificates in nginx/ssl/$(NC)"
	@echo "  - cert.pem (certificate)"
	@echo "  - key.pem (private key)"
	@echo "  - chain.pem (certificate chain)"

##@ Backup & Restore
backup: ## Backup database
	@echo "$(GREEN)💾 Creating database backup for $(ENV) environment...$(NC)"
	@mkdir -p backups
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec postgres pg_dump -U postgres -d horizon_digital_$(ENV) > backups/backup_$(ENV)_$(shell date +%Y%m%d_%H%M%S).sql
	@echo "$(GREEN)✅ Database backup created$(NC)"

restore: ## Restore database (usage: make restore BACKUP_FILE=backup.sql)
	@if [ -z "$(BACKUP_FILE)" ]; then \
		echo "$(RED)❌ Please specify BACKUP_FILE. Usage: make restore BACKUP_FILE=backup.sql$(NC)"; \
		exit 1; \
	fi
	@echo "$(YELLOW)⚠️  This will overwrite the current database!$(NC)"
	@read -p "Are you sure? (y/N): " confirm && [ "$$confirm" = "y" ] || exit 1
	@echo "$(GREEN)📥 Restoring database from $(BACKUP_FILE)...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec -T postgres psql -U postgres -d horizon_digital_$(ENV) < $(BACKUP_FILE)
	@echo "$(GREEN)✅ Database restored$(NC)"

##@ Quick Start Commands
dev: ## Quick start development environment
	@echo "$(GREEN)🚀 Quick starting development environment...$(NC)"
	@if [ ! -f .env ]; then \
		echo "$(BLUE)🔄 Creating .env file for development...$(NC)"; \
		./env-switch.sh development; \
	else \
		echo "$(YELLOW)📝 .env file exists, skipping environment switch$(NC)"; \
		echo "$(YELLOW)💡 Use 'make env-switch ENV=development' to reset environment$(NC)"; \
	fi
	@make build ENV=development
	@make start ENV=development
	@echo "$(GREEN)✅ Development environment is ready!$(NC)"
	@echo "$(YELLOW)🌐 Frontend: http://localhost:8080$(NC)"
	@echo "$(YELLOW)🐍 Backend API: http://localhost:8080/api/$(NC)"
	@echo "$(YELLOW)🔧 Admin: http://localhost:8080/admin/$(NC)"

prod: ## Quick start production environment
	@echo "$(GREEN)🚀 Quick starting production environment...$(NC)"
	@if [ ! -f .env ]; then \
		echo "$(BLUE)🔄 Creating .env file for production...$(NC)"; \
		./env-switch.sh production; \
	else \
		echo "$(YELLOW)📝 .env file exists, skipping environment switch$(NC)"; \
		echo "$(YELLOW)💡 Use 'make env-switch ENV=production' to reset environment$(NC)"; \
	fi
	@make build ENV=production
	@make start ENV=production
	@echo "$(GREEN)✅ Production environment is ready!$(NC)"

uat: ## Quick start UAT environment
	@echo "$(GREEN)🚀 Quick starting UAT environment...$(NC)"
	@if [ ! -f .env ]; then \
		echo "$(BLUE)🔄 Creating .env file for UAT...$(NC)"; \
		./env-switch.sh uat; \
	else \
		echo "$(YELLOW)📝 .env file exists, skipping environment switch$(NC)"; \
		echo "$(YELLOW)💡 Use 'make env-switch ENV=uat' to reset environment$(NC)"; \
	fi
	@make build ENV=uat
	@make start ENV=uat
	@echo "$(GREEN)✅ UAT environment is ready!$(NC)"

##@ Utility Commands
ps: ## Show running containers
	@echo "$(BLUE)🐳 Showing running containers for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) ps

images: ## Show Docker images
	@echo "$(BLUE)🖼️  Showing Docker images for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) images

exec-backend: ## Execute command in backend container (usage: make exec-backend CMD="command")
	@if [ -z "$(CMD)" ]; then \
		echo "$(RED)❌ Please specify CMD. Usage: make exec-backend CMD=\"command\"$(NC)"; \
		exit 1; \
	fi
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) exec backend $(CMD)

up-logs: ## Start all services and follow logs
	@echo "$(GREEN)🚀 Starting services with logs for $(ENV) environment...$(NC)"
	docker-compose -f $(COMPOSE_FILES) --env-file $(ENV_FILE) up

##@ Monitoring
health: ## Check health of all services
	@echo "$(BLUE)🏥 Checking service health for $(ENV) environment...$(NC)"
	@echo "$(YELLOW)Backend Health:$(NC)"
	@curl -s http://localhost:8080/api/health/ || echo "$(RED)Backend not responding$(NC)"
	@echo ""
	@echo "$(YELLOW)Nginx Health:$(NC)"
	@curl -s http://localhost:8080/health || echo "$(RED)Nginx not responding$(NC)"
	@echo ""

monitor: ## Show real-time resource usage
	@echo "$(BLUE)📊 Monitoring resource usage for $(ENV) environment...$(NC)"
	docker stats --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}"

##@ Information
info: ## Show environment information
	@echo "$(BLUE)ℹ️  Environment Information$(NC)"
	@echo "================================"
	@echo "Current Environment: $(ENV)"
	@echo "Compose Files: $(COMPOSE_FILES)"
	@echo "Environment File: $(ENV_FILE)"
	@echo "Docker Version: $(shell docker --version)"
	@echo "Docker Compose Version: $(shell docker-compose --version)"
	@echo ""
	@if [ -f .env ]; then \
		echo "Project Name: $(shell grep 'PROJECT_NAME=' .env | cut -d'=' -f2)"; \
		echo "Current Env in .env: $(shell grep 'ENVIRONMENT=' .env | cut -d'=' -f2)"; \
	fi
	@echo ""
	@echo "$(YELLOW)Available Environments:$(NC)"
	@echo "  - development (Development)"
	@echo "  - uat         (User Acceptance Testing)"
	@echo "  - production  (Production)"
	@echo ""
	@echo "$(YELLOW)Switch Environment:$(NC)"
	@echo "  ./env-switch.sh development"
	@echo "  ./env-switch.sh uat"
	@echo "  ./env-switch.sh production"
