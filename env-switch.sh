#!/bin/bash

# Environment Switching Script for Horizon Digital Monorepo
# This script helps manage environment configurations

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to display usage
usage() {
    echo -e "${BLUE}Environment Switching Script${NC}"
    echo "Usage: $0 <environment>"
    echo ""
    echo "Available environments:"
    echo "  development  - Development environment"
    echo "  uat         - User Acceptance Testing environment"
    echo "  production  - Production environment"
    echo ""
    echo "Examples:"
    echo "  $0 development"
    echo "  $0 uat"
    echo "  $0 production"
    exit 1
}

# Check if environment argument is provided
if [ $# -eq 0 ]; then
    echo -e "${RED}❌ No environment specified!${NC}"
    usage
fi

ENVIRONMENT=$1

# Validate environment
case $ENVIRONMENT in
    development|dev)
        ENVIRONMENT="development"
        DB_NAME="horizon_digital_dev"
        POSTGRES_PORT="5455"
        REDIS_PORT="6380"
        API_URL="http://localhost:8080/api"
        APP_NAME="Horizon Digital (Dev)"
        NGINX_HTTP_PORT="8080"
        BACKEND_PORT="8000"
        FRONTEND_PORT="3000"
        DEBUG="True"
        SECRET_KEY="dev-secret-key-not-for-production-use"
        ALLOWED_HOSTS="localhost,127.0.0.1,0.0.0.0,backend"
        CORS_ORIGINS="http://localhost:8080,http://127.0.0.1:8080"
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend"
        LOG_LEVEL="DEBUG"
        SECURE_SSL_REDIRECT="False"
        ;;
    uat)
        ENVIRONMENT="uat"
        DB_NAME="horizon_digital_uat"
        POSTGRES_PORT="5455"
        REDIS_PORT="6381"
        API_URL="https://uat-api.horizondigital.com"
        APP_NAME="Horizon Digital (UAT)"
        NGINX_HTTP_PORT="8081"
        BACKEND_PORT="8002"
        FRONTEND_PORT="3002"
        DEBUG="False"
        SECRET_KEY="uat-secret-key-change-this"
        ALLOWED_HOSTS="uat.horizondigital.com,localhost,127.0.0.1"
        CORS_ORIGINS="https://uat.horizondigital.com,http://localhost:8081"
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"
        LOG_LEVEL="INFO"
        SECURE_SSL_REDIRECT="False"
        ;;
    production|prod)
        ENVIRONMENT="production"
        DB_NAME="horizon_digital_prod"
        POSTGRES_PORT="5432"
        REDIS_PORT="6379"
        API_URL="https://api.horizondigital.com"
        APP_NAME="Horizon Digital"
        NGINX_HTTP_PORT="80"
        NGINX_HTTPS_PORT="443"
        BACKEND_PORT="8000"
        FRONTEND_PORT="3000"
        DEBUG="False"
        SECRET_KEY="production-secret-key-generate-strong-key"
        ALLOWED_HOSTS="horizondigital.com,www.horizondigital.com,api.horizondigital.com"
        CORS_ORIGINS="https://horizondigital.com,https://www.horizondigital.com"
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend"
        LOG_LEVEL="WARNING"
        SECURE_SSL_REDIRECT="True"
        ;;
    *)
        echo -e "${RED}❌ Invalid environment: $ENVIRONMENT${NC}"
        usage
        ;;
esac

echo -e "${BLUE}🔄 Switching to $ENVIRONMENT environment...${NC}"

# Create .env file from template if it doesn't exist
if [ ! -f .env ]; then
    if [ -f env.example ]; then
        cp env.example .env
        echo -e "${GREEN}✅ Created .env from env.example${NC}"
    else
        echo -e "${RED}❌ env.example not found!${NC}"
        exit 1
    fi
fi

# Update environment-specific variables
update_env_var() {
    local var_name=$1
    local var_value=$2
    
    if grep -q "^${var_name}=" .env; then
        # Variable exists, update it
        sed -i.bak "s|^${var_name}=.*|${var_name}=${var_value}|" .env
    else
        # Variable doesn't exist, add it
        echo "${var_name}=${var_value}" >> .env
    fi
}

# Update basic environment settings
update_env_var "ENVIRONMENT" "$ENVIRONMENT"
update_env_var "DEBUG" "$DEBUG"
update_env_var "SECRET_KEY" "$SECRET_KEY"

# Update database settings
update_env_var "POSTGRES_DB" "$DB_NAME"
update_env_var "POSTGRES_PORT" "$POSTGRES_PORT"
update_env_var "DATABASE_URL" "postgresql://\${POSTGRES_USER:-postgres}:\${POSTGRES_PASSWORD:-postgres}@postgres:5432/$DB_NAME"

# Update Redis settings
update_env_var "REDIS_PORT" "$REDIS_PORT"

# Update frontend settings
update_env_var "VITE_API_BASE_URL" "$API_URL"
update_env_var "VITE_APP_NAME" "$APP_NAME"

# Update service ports
update_env_var "NGINX_HTTP_PORT" "$NGINX_HTTP_PORT"
if [ -n "$NGINX_HTTPS_PORT" ]; then
    update_env_var "NGINX_HTTPS_PORT" "$NGINX_HTTPS_PORT"
fi
update_env_var "BACKEND_PORT" "$BACKEND_PORT"
update_env_var "FRONTEND_PORT" "$FRONTEND_PORT"

# Update Django settings
update_env_var "ALLOWED_HOSTS" "$ALLOWED_HOSTS"
update_env_var "CORS_ALLOWED_ORIGINS" "$CORS_ORIGINS"
update_env_var "EMAIL_BACKEND" "$EMAIL_BACKEND"
update_env_var "LOG_LEVEL" "$LOG_LEVEL"
update_env_var "SECURE_SSL_REDIRECT" "$SECURE_SSL_REDIRECT"

# Update database settings
update_env_var "DATABASE_ENGINE" "django.db.backends.postgresql"
update_env_var "DATABASE_URL" "postgresql://\${POSTGRES_USER:-postgres}:\${POSTGRES_PASSWORD:-postgres}@postgres:5432/$DB_NAME"
update_env_var "DB_ENGINE" "django.db.backends.postgresql"
update_env_var "DB_NAME" "$DB_NAME"
update_env_var "DB_USER" "\${POSTGRES_USER:-postgres}"
update_env_var "DB_PASSWORD" "\${POSTGRES_PASSWORD:-postgres}"
update_env_var "DB_HOST" "postgres"
update_env_var "DB_PORT" "5432"

# Additional common Django database environment variables
update_env_var "DJANGO_DATABASE_ENGINE" "django.db.backends.postgresql"
update_env_var "DJANGO_DATABASE_NAME" "$DB_NAME"
update_env_var "DJANGO_DATABASE_USER" "\${POSTGRES_USER:-postgres}"
update_env_var "DJANGO_DATABASE_PASSWORD" "\${POSTGRES_PASSWORD:-postgres}"
update_env_var "DJANGO_DATABASE_HOST" "postgres"
update_env_var "DJANGO_DATABASE_PORT" "5432"

# Environment-specific configurations
case $ENVIRONMENT in
    development)
        update_env_var "CSRF_TRUSTED_ORIGINS" "http://localhost:8080,http://127.0.0.1:8080"
        update_env_var "DJANGO_LOG_LEVEL" "DEBUG"
        update_env_var "SECURE_HSTS_SECONDS" "0"
        update_env_var "SESSION_COOKIE_SECURE" "False"
        update_env_var "CSRF_COOKIE_SECURE" "False"
        ;;
    uat)
        update_env_var "CSRF_TRUSTED_ORIGINS" "https://uat.horizondigital.com,http://localhost:8081"
        update_env_var "DJANGO_LOG_LEVEL" "INFO"
        update_env_var "SECURE_HSTS_SECONDS" "3600"
        update_env_var "SESSION_COOKIE_SECURE" "True"
        update_env_var "CSRF_COOKIE_SECURE" "True"
        # Add placeholders for UAT-specific values
        if ! grep -q "^POSTGRES_PASSWORD=" .env; then
            update_env_var "POSTGRES_PASSWORD" "change-this-password"
        fi
        if ! grep -q "^REDIS_PASSWORD=" .env; then
            update_env_var "REDIS_PASSWORD" "change-this-redis-password"
        fi
        ;;
    production)
        update_env_var "CSRF_TRUSTED_ORIGINS" "https://horizondigital.com,https://www.horizondigital.com"
        update_env_var "DJANGO_LOG_LEVEL" "WARNING"
        update_env_var "SECURE_HSTS_SECONDS" "31536000"
        update_env_var "SECURE_HSTS_INCLUDE_SUBDOMAINS" "True"
        update_env_var "SECURE_HSTS_PRELOAD" "True"
        update_env_var "SESSION_COOKIE_SECURE" "True"
        update_env_var "CSRF_COOKIE_SECURE" "True"
        # Add placeholders for production-specific values
        if ! grep -q "^POSTGRES_PASSWORD=" .env; then
            update_env_var "POSTGRES_PASSWORD" "super-secure-production-password"
        fi
        if ! grep -q "^REDIS_PASSWORD=" .env; then
            update_env_var "REDIS_PASSWORD" "super-secure-redis-password"
        fi
        ;;
esac

# Clean up backup file created by sed
rm -f .env.bak

echo -e "${GREEN}✅ Environment switched to $ENVIRONMENT${NC}"
echo -e "${YELLOW}📝 Please review and update .env file with your specific values:${NC}"

case $ENVIRONMENT in
    development)
        echo "  - Basic development setup is ready"
        echo "  - Access: http://localhost:8080"
        ;;
    uat)
        echo "  - Update POSTGRES_PASSWORD and REDIS_PASSWORD"
        echo "  - Configure EMAIL_HOST_USER and EMAIL_HOST_PASSWORD"
        echo "  - Update domain names if different"
        ;;
    production)
        echo "  - Update POSTGRES_PASSWORD and REDIS_PASSWORD with strong passwords"
        echo "  - Configure EMAIL_HOST_USER and EMAIL_HOST_PASSWORD"
        echo "  - Set up SSL certificates"
        echo "  - Configure monitoring and backup settings"
        echo "  - Update domain names"
        ;;
esac

echo ""
echo -e "${BLUE}Next steps:${NC}"
echo "  1. Review .env file: nano .env"
echo "  2. Build services: make build ENV=$ENVIRONMENT"
echo "  3. Start services: make start ENV=$ENVIRONMENT"
