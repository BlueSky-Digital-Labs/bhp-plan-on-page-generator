#!/bin/bash

# Horizon Digital Monorepo Setup Script
# This script helps initialize the development environment

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}"
echo "🌟 Monorepo Setup"
echo "=================================="
echo -e "${NC}"

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker is not installed. Please install Docker first.${NC}"
    echo "Visit: https://docs.docker.com/get-docker/"
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}❌ Docker Compose is not installed. Please install Docker Compose first.${NC}"
    echo "Visit: https://docs.docker.com/compose/install/"
    exit 1
fi

# Check if Make is installed
if ! command -v make &> /dev/null; then
    echo -e "${YELLOW}⚠️  Make is not installed. You can still use Docker Compose commands directly.${NC}"
fi

echo -e "${GREEN}✅ Docker and Docker Compose are installed${NC}"

# Create environment file
echo -e "${BLUE}📝 Setting up environment...${NC}"

if [ ! -f .env ]; then
    ./env-switch.sh development
    echo -e "${GREEN}✅ Created .env for development${NC}"
else
    echo -e "${YELLOW}⚠️  .env already exists${NC}"
fi

# Create required directories
echo -e "${BLUE}📁 Creating required directories...${NC}"
mkdir -p nginx/ssl nginx/logs backups
echo -e "${GREEN}✅ Directories created${NC}"

# Set permissions for scripts
chmod +x setup.sh

echo -e "${BLUE}🐳 Building Docker images...${NC}"
if command -v make &> /dev/null; then
    make build ENV=development
else
    docker-compose -f docker-compose.yml -f docker-compose.dev.yml build
fi

echo -e "${BLUE}🚀 Starting development environment...${NC}"
if command -v make &> /dev/null; then
    make start ENV=development
else
    docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d
fi

# Wait for services to be ready
echo -e "${BLUE}⏳ Waiting for services to be ready...${NC}"
sleep 10

# Run initial setup commands
echo -e "${BLUE}🔧 Running initial setup...${NC}"

# Run migrations
if command -v make &> /dev/null; then
    make migrate ENV=development
else
    docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py migrate
fi

# Collect static files
if command -v make &> /dev/null; then
    make collectstatic ENV=development
else
    docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py collectstatic --noinput
fi

echo -e "${GREEN}"
echo "✅ Setup completed successfully!"
echo ""
echo "🌐 Your application is now running:"
echo "   Frontend: http://localhost:8080"
echo "   Backend API: http://localhost:8080/api"
echo "   Admin Panel: http://localhost:8080/admin"
echo ""
echo "📚 Next steps:"
echo "   1. Create a superuser: make createsuperuser ENV=development"
echo "   2. View logs: make logs ENV=development"
echo "   3. Stop services: make stop ENV=development"
echo ""
echo "📖 For more commands, run: make help"
echo -e "${NC}"

# Prompt to create superuser
read -p "Would you like to create a Django superuser now? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    if command -v make &> /dev/null; then
        make createsuperuser ENV=development
    else
        docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python manage.py createsuperuser
    fi
fi

echo -e "${GREEN}🎉 Horizon Digital is ready for development!${NC}"
