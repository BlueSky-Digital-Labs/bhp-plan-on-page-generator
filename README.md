# 🌟 Horizon Digital Monorepo

A production-ready full-stack monorepo featuring Django backend and React frontend with comprehensive Docker orchestration, Nginx reverse proxy, and multi-environment support.

## 📋 Table of Contents

- [🎯 Overview](#-overview)
- [🏗️ Architecture](#️-architecture)
- [🚀 Quick Start](#-quick-start)
- [🐳 Docker Orchestration](#-docker-orchestration)
- [🌍 Environment Management](#-environment-management)
- [📖 Usage](#-usage)
- [🔧 Development](#-development)
- [🚀 Deployment](#-deployment)
- [📊 Monitoring](#-monitoring)
- [🔐 Security](#-security)
- [🛠️ Troubleshooting](#️-troubleshooting)

## 🎯 Overview

This monorepo provides a complete full-stack solution with:

### Backend (Django)
- **Django 5.x** with REST API
- **PostgreSQL** database
- **Redis** for caching and Celery
- **Celery** for background tasks
- **JWT Authentication**
- **Docker containerization**

### Frontend (React)
- **React 19** with TypeScript
- **Vite** for fast development
- **Redux Toolkit** for state management
- **Atomic Design** component architecture
- **Comprehensive UI system**

### Infrastructure
- **Nginx** reverse proxy with SSL
- **Docker Compose** orchestration
- **Multi-environment** support (Dev/UAT/Prod)
- **Automated backups**
- **Health monitoring**

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         Nginx Reverse Proxy                 │
│                    (Load Balancer & SSL)                   │
└─────────────────┬───────────────────────┬───────────────────┘
                  │                       │
         ┌────────▼────────┐    ┌────────▼────────┐
         │  React Frontend │    │ Django Backend  │
         │   (Port 3000)   │    │  (Port 8000)   │
         │                 │    │                 │
         │ • React 19      │    │ • Django 5.x    │
         │ • TypeScript    │    │ • DRF API       │
         │ • Vite          │    │ • JWT Auth      │
         │ • Redux Toolkit │    │ • Celery Tasks  │
         └─────────────────┘    └─────┬───────────┘
                                      │
                         ┌────────────▼────────────┐
                         │     Supporting Services │
                         │                         │
                         │ ┌─────────┐ ┌─────────┐ │
                         │ │PostgreSQL│ │  Redis  │ │
                         │ │Database │ │ Cache   │ │
                         │ └─────────┘ └─────────┘ │
                         │                         │
                         │ ┌─────────┐ ┌─────────┐ │
                         │ │ Celery  │ │ Celery  │ │
                         │ │ Worker  │ │  Beat   │ │
                         │ └─────────┘ └─────────┘ │
                         └─────────────────────────┘
```

## 🚀 Quick Start

### Prerequisites
- **Docker** (20.10+)
- **Docker Compose** (v2.0+)
- **Make** (for simplified commands)

### 1. Clone Repository with Submodules
```bash
# Clone repository with all submodules
git clone --recurse-submodules <repository-url>
cd next-js-django-monorepo-starter
```

### 2. Setup Environment Configuration
```bash
# Copy environment template and configure for development
cp env.example .env

# OR use the environment switcher for automatic configuration
./env-switch.sh dev
```

**Configure your `.env` file**:
- Set `PROJECT_NAME` for custom container naming (default: horizon-digital)
- Update database credentials (`POSTGRES_PASSWORD`, etc.)
- Configure API URLs (`VITE_API_BASE_URL`)
- Set security settings for your environment
- Update service ports if needed

### 3. Start Development Environment
```bash
# Quick setup development environment
./setup.sh
# OR
make dev
```

### 4. Access Your Application
- **Frontend**: http://localhost:8080
- **Backend API**: http://localhost:8080/api
- **Admin Panel**: http://localhost:8080/admin

### 5. Create Admin User
```bash
make createsuperuser ENV=development
```

## 📚 Git Submodules Management

This monorepo uses Git submodules to manage backend and frontend as separate repositories:

```
next-js-django-monorepo-starter/
├── backend/          # Django backend (Git submodule)
├── frontend/         # React frontend (Git submodule)
├── nginx/            # Nginx configuration
├── docker-compose.*  # Docker orchestration files
└── Makefile          # Management commands
```

### Working with Submodules

```bash
# Update all submodules to latest
git submodule update --remote

# Update specific submodule
git submodule update --remote backend

# Pull main repo with submodule updates
git pull --recurse-submodules

# Check submodule status
git submodule status
```

### Making Changes to Submodules

```bash
# Navigate to submodule
cd backend  # or frontend

# Create feature branch
git checkout -b feature/my-feature

# Make changes, commit and push
git add .
git commit -m "feat: add new feature"
git push origin feature/my-feature

# Return to main repo and commit submodule update
cd ..
git add backend
git commit -m "update backend submodule"
git push origin main
```

## 🐳 Docker Orchestration

### Service Architecture

| Service | Purpose | Port | Health Check |
|---------|---------|------|--------------|
| **nginx** | Reverse proxy & SSL | 80, 443 | /health |
| **frontend** | React application | 3000 | - |
| **backend** | Django API server | 8000 | /health/ |
| **postgres** | Primary database | 5432 | pg_isready |
| **redis** | Cache & message broker | 6379 | ping |
| **celery_worker** | Background tasks | - | - |
| **celery_beat** | Task scheduler | - | - |

### Docker Compose Files

- **`docker-compose.yml`** - Base configuration
- **`docker-compose.dev.yml`** - Development overrides
- **`docker-compose.uat.yml`** - UAT environment
- **`docker-compose.prod.yml`** - Production configuration

## 🌍 Environment Management

### Available Environments

#### 🔧 Development (`dev`)
- **Purpose**: Local development
- **Access**: http://localhost:8080
- **Features**: Hot reload, debug mode, console email backend
- **Database**: horizon_digital_dev

#### 🧪 UAT (`uat`)
- **Purpose**: User acceptance testing
- **Access**: http://localhost:8081
- **Features**: Production-like with testing flexibility
- **Database**: horizon_digital_uat

#### 🚀 Production (`prod`)
- **Purpose**: Live production environment
- **Access**: https://horizondigital.com
- **Features**: SSL, security headers, optimized performance
- **Database**: horizon_digital_prod

### Environment Configuration

#### **🔄 Unified Environment Management**

This monorepo uses a **single `.env` file** with **environment switching** for simplified management:

1. **Switch environments easily**:
```bash
# Switch to development
./env-switch.sh development

# Switch to UAT
./env-switch.sh uat

# Switch to production
./env-switch.sh production
```

2. **Container naming**: All containers use your project name from `.env`:
```bash
PROJECT_NAME=horizon-digital
# Results in containers like: horizon-digital_backend, horizon-digital_frontend, etc.
```

3. **Key configurations**:
   - **Project name**: Customizable container and network naming
   - **Database credentials**: Environment-specific
   - **Redis passwords**: Secure per environment
   - **API URLs**: Development/staging/production endpoints
   - **Security settings**: Environment-appropriate security levels
   - **Email configuration**: Per-environment email backends

## 📖 Usage

### Core Commands

```bash
# Environment setup
make setup                         # Create development environment
./env-switch.sh [environment]      # Switch environments
make env-check ENV=development     # Validate environment

# Service management
make start ENV=development         # Start all services
make stop ENV=development          # Stop all services
make down ENV=development          # Alias for stop command
make restart ENV=development       # Restart services
make status ENV=development        # Show service status
make up-logs ENV=development       # Start services and follow logs

# Individual services
make start-backend ENV=development # Start only backend services
make start-frontend ENV=development# Start only frontend
make start-nginx ENV=development   # Start only nginx

# Database operations
make migrate ENV=development       # Run migrations
make makemigrations ENV=development# Create migrations
make collectstatic ENV=development # Collect static files
make createsuperuser ENV=development# Create admin user
make dbshell ENV=development       # Open database shell

# API Documentation
make schema ENV=development        # Generate OpenAPI schema
make docs                          # Show API documentation URLs

# Development tools
make shell ENV=development         # Django shell
make shell-backend ENV=development # Backend container bash
make shell-frontend ENV=development# Frontend container bash
make shell-nginx ENV=development   # Nginx container bash
make shell-postgres ENV=development# Postgres container bash
make shell-redis ENV=development   # Redis CLI

# Testing
make test ENV=development          # Run all tests
make test-backend ENV=development  # Run backend tests only
make test-coverage ENV=development # Run tests with coverage

# Code quality
make lint ENV=development          # Run linting
make format ENV=development        # Format code
make install ENV=development       # Install dependencies

# Utility commands
make ps ENV=development            # Show running containers
make images ENV=development        # Show Docker images
make exec-backend CMD="command"    # Execute command in backend container

# Monitoring
make logs ENV=development          # View all logs
make logs-backend ENV=development  # Backend logs only
make logs-frontend ENV=development # Frontend logs only
make logs-nginx ENV=development    # Nginx logs only
make health ENV=development        # Check service health
make monitor ENV=development       # Resource usage

# Maintenance
make backup ENV=development        # Database backup
make restore BACKUP_FILE=file.sql  # Restore database
make clean                         # Clean Docker resources
make clean-all                     # Remove all containers/images
make update ENV=development        # Pull latest images
```

### Quick Environment Commands

```bash
make dev                     # Start development environment
make uat                     # Start UAT environment
make prod                    # Start production environment
```

### 📋 Make Command Help

To see all available commands with descriptions:
```bash
make help                    # Show comprehensive help with categories
make info                    # Show environment information
```

The Makefile is organized into logical sections:
- **Environment Management**: Setup and switching between environments
- **Docker Operations**: Build, start, stop, restart services
- **Individual Service Management**: Control specific services
- **Database Operations**: Migrations, admin users, backups
- **Development Tools**: Shells, debugging, API docs
- **Code Quality**: Testing, linting, formatting
- **Utility Commands**: Container inspection, execution
- **Monitoring**: Health checks, logs, resource usage
- **Maintenance**: Cleanup, updates, SSL setup

### 🔧 Troubleshooting & Debugging

#### Container Management
```bash
# Check container status
make ps ENV=development
make status ENV=development

# View logs
make logs ENV=development              # All services
make logs-backend ENV=development      # Backend only
make logs-frontend ENV=development     # Frontend only
make logs-nginx ENV=development        # Nginx only

# Access container shells
make shell-backend ENV=development     # Backend container bash
make shell-frontend ENV=development    # Frontend container shell
make shell-postgres ENV=development    # Database container
make shell-redis ENV=development       # Redis CLI

# Health checks
make health ENV=development            # Check all services
curl http://localhost:8080/api/health/ # Backend health
curl http://localhost:8080/health      # Nginx health
```

#### Common Issues & Solutions
```bash
# 1. Containers won't start
make down ENV=development
make clean
make start ENV=development

# 2. Database connection issues
make shell-postgres ENV=development    # Check database
make dbshell ENV=development          # Django database shell
make migrate ENV=development          # Run migrations

# 3. Frontend build issues
make shell-frontend ENV=development   # Access frontend container
# Inside container: npm install or bun install

# 4. Permission issues
make exec-backend CMD="chown -R $(id -u):$(id -g) /app"

# 5. Port conflicts
make stop ENV=development            # Stop services
docker ps                            # Check for conflicting containers
make start ENV=development           # Restart

# 6. Complete reset
make clean-all                       # WARNING: Removes everything
make dev                            # Fresh start
```

#### Performance Monitoring
```bash
make monitor ENV=development         # Real-time resource usage
make ps ENV=development             # Container status
make images ENV=development         # Image sizes
```

## 🔧 Development

### Local Development Workflow

1. **Start development environment**:
```bash
make dev
```

2. **Make code changes** in `backend/` or `frontend/`

3. **View live changes**:
   - Frontend: Hot module replacement (HMR)
   - Backend: Auto-reload on file changes

4. **Run tests**:
```bash
make test ENV=dev
```

5. **Code quality**:
```bash
make lint ENV=dev            # Run linting
make format ENV=dev          # Format code
```

### Development URLs

- **Main Application**: http://localhost:8080
- **Backend API**: http://localhost:8080/api
- **Django Admin**: http://localhost:8080/admin
- **API Documentation**: http://localhost:8080/api/docs (if configured)

### Backend Development

- **Location**: `./backend/`
- **Documentation**: See `./backend/README.md`
- **Framework**: Django with DRF
- **Database**: PostgreSQL
- **Background Tasks**: Celery with Redis

### Frontend Development

- **Location**: `./frontend/`
- **Documentation**: See `./frontend/README.md`
- **Framework**: React 19 with TypeScript
- **Build Tool**: Vite
- **State Management**: Redux Toolkit

## 🚀 Deployment

### Production Deployment

1. **Prepare environment**:
```bash
cp env.prod.example .env.prod
# Configure production values
```

2. **Setup SSL certificates**:
```bash
make ssl-setup ENV=prod
# Place certificates in nginx/ssl/
```

3. **Deploy**:
```bash
make prod
```

4. **Post-deployment**:
```bash
make migrate ENV=prod
make collectstatic ENV=prod
make createsuperuser ENV=prod
```

### UAT Deployment

```bash
cp env.uat.example .env.uat
# Configure UAT values
make uat
make migrate ENV=uat
```

### SSL Configuration

For production, place your SSL certificates in `nginx/ssl/`:
- `cert.pem` - SSL certificate
- `key.pem` - Private key
- `chain.pem` - Certificate chain

## 📊 Monitoring

### Health Checks

```bash
make health ENV=prod         # Check all services
curl http://localhost/health # Nginx health
curl http://localhost:8000/health/ # Backend health
```

### Logs

```bash
make logs ENV=prod           # All services
make logs-nginx ENV=prod     # Nginx only
make logs-backend ENV=prod   # Backend only
make monitor ENV=prod        # Resource usage
```

### Backup & Restore

```bash
# Create backup
make backup ENV=prod

# Restore from backup
make restore BACKUP_FILE=backup_prod_20231201_120000.sql ENV=prod
```

## 🔐 Security

### Security Features

- **SSL/TLS** encryption with strong ciphers
- **Security headers** (HSTS, CSP, X-Frame-Options)
- **Rate limiting** for API endpoints
- **CORS** protection
- **Environment isolation**
- **Secret management** via environment variables

### Production Security Checklist

- [ ] Strong passwords for all services
- [ ] SSL certificates configured
- [ ] Environment variables secured
- [ ] Admin access restricted
- [ ] Regular security updates
- [ ] Backup encryption enabled
- [ ] Monitoring and alerting configured

### Environment Security

#### Development
- Basic security for local development
- Debug mode enabled
- Console email backend

#### UAT
- Moderate security for testing
- HTTPS optional
- Email backend configured

#### Production
- Maximum security
- HTTPS enforced
- HSTS headers
- Secure cookies
- Rate limiting
- IP restrictions for admin

## 🛠️ Troubleshooting

### Common Issues

#### Services Not Starting

```bash
# Check service status
make status ENV=dev

# Check logs for errors
make logs ENV=dev

# Restart services
make restart ENV=dev
```

#### Database Connection Issues

```bash
# Check database health
docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec postgres pg_isready

# Reset database
make stop ENV=dev
docker volume rm next-js-django-monorepo-starter_postgres_dev_data
make start ENV=dev
make migrate ENV=dev
```

#### Frontend Build Issues

```bash
# Rebuild frontend
docker-compose -f docker-compose.yml -f docker-compose.dev.yml build frontend

# Check frontend logs
make logs-frontend ENV=dev
```

#### Nginx Configuration Issues

```bash
# Test nginx configuration
docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec nginx nginx -t

# Reload nginx
docker-compose -f docker-compose.yml -f docker-compose.dev.yml exec nginx nginx -s reload
```

### Debug Commands

```bash
# Environment information
make info ENV=dev

# Container inspection
docker-compose -f docker-compose.yml -f docker-compose.dev.yml ps
docker-compose -f docker-compose.yml -f docker-compose.dev.yml top

# Network inspection
docker network ls
docker network inspect next-js-django-monorepo-starter_horizon_dev_network
```

### Performance Tuning

#### Database Optimization
- Adjust PostgreSQL configuration
- Implement connection pooling
- Optimize queries and indexes

#### Redis Optimization
- Configure memory limits
- Set appropriate eviction policies
- Monitor memory usage

#### Nginx Optimization
- Enable gzip compression
- Configure caching headers
- Optimize worker processes

## 📚 Additional Resources

### Documentation
- [Backend README](./backend/README.md)
- [Frontend README](./frontend/README.md)
- [Django Documentation](https://docs.djangoproject.com/)
- [React Documentation](https://react.dev/)

### Monitoring Tools
- Docker stats: `docker stats`
- Container logs: `docker logs <container>`
- System resources: `htop`, `iostat`

### External Services Integration
- **Email**: SMTP configuration
- **File Storage**: AWS S3 or similar
- **Monitoring**: Sentry, DataDog
- **Analytics**: Google Analytics

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit your changes: `git commit -m 'Add amazing feature'`
4. Push to the branch: `git push origin feature/amazing-feature`
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

For support and questions:
- Create an issue in the GitHub repository
- Check the troubleshooting section
- Review component documentation in `backend/` and `frontend/` folders

---

**Built with ❤️ for modern full-stack development**
