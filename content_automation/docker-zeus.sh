#!/bin/bash

# Zeus Framework Docker Management Script
set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}🏛️ Zeus Framework Docker Manager${NC}"
echo "====================================="

# Check if docker and docker-compose are installed
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker not found. Please install Docker first.${NC}"
    exit 1
fi

if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
    echo -e "${RED}❌ Docker Compose not found. Please install Docker Compose first.${NC}"
    exit 1
fi

# Use docker compose or docker-compose based on availability
COMPOSE_CMD="docker compose"
if ! docker compose version &> /dev/null; then
    COMPOSE_CMD="docker-compose"
fi

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠️ .env file not found${NC}"
    echo "Copying .env.docker to .env..."
    cp .env.docker .env
    echo -e "${RED}❌ Please edit .env with your API keys before continuing${NC}"
    echo "Required: OPENROUTER_API_KEY (you already have this)"
    echo "Optional (for full functionality): FAL_API_KEY, ELEVENLABS_API_KEY, PUBLER_API_KEY"
    exit 1
fi

# Parse command line arguments
case "${1:-help}" in
    "build")
        echo -e "${BLUE}🔨 Building Zeus Framework images...${NC}"
        $COMPOSE_CMD build --no-cache
        ;;
    "up"|"start")
        echo -e "${GREEN}🚀 Starting Zeus Framework...${NC}"
        $COMPOSE_CMD up -d
        echo ""
        echo -e "${GREEN}✅ Zeus Framework is running!${NC}"
        echo ""
        echo "🔗 Services:"
        echo "  📊 Monitor Dashboard: http://localhost:8080"
        echo "  🗄️ PostgreSQL: localhost:5432"
        echo "  🔴 Redis: localhost:6379"
        echo ""
        echo "📋 Useful commands:"
        echo "  ./docker-zeus.sh logs     - View logs"
        echo "  ./docker-zeus.sh status   - Check status"
        echo "  ./docker-zeus.sh stop     - Stop services"
        ;;
    "stop")
        echo -e "${YELLOW}🛑 Stopping Zeus Framework...${NC}"
        $COMPOSE_CMD stop
        echo -e "${GREEN}✅ Stopped successfully${NC}"
        ;;
    "down")
        echo -e "${YELLOW}🗑️ Stopping and removing containers...${NC}"
        $COMPOSE_CMD down
        echo -e "${GREEN}✅ Containers removed${NC}"
        ;;
    "restart")
        echo -e "${YELLOW}🔄 Restarting Zeus Framework...${NC}"
        $COMPOSE_CMD restart
        echo -e "${GREEN}✅ Restarted successfully${NC}"
        ;;
    "logs")
        service="${2:-zeus-app}"
        echo -e "${BLUE}📝 Showing logs for ${service}...${NC}"
        $COMPOSE_CMD logs -f "$service"
        ;;
    "status")
        echo -e "${BLUE}📊 Zeus Framework Status${NC}"
        echo "========================"
        $COMPOSE_CMD ps
        ;;
    "shell")
        service="${2:-zeus-app}"
        echo -e "${BLUE}🐚 Opening shell in ${service}...${NC}"
        $COMPOSE_CMD exec "$service" /bin/bash
        ;;
    "db")
        echo -e "${BLUE}🗄️ Connecting to database...${NC}"
        $COMPOSE_CMD exec postgres psql -U zeus -d zeus_content
        ;;
    "backup")
        echo -e "${BLUE}💾 Creating database backup...${NC}"
        timestamp=$(date +"%Y%m%d_%H%M%S")
        mkdir -p backups
        $COMPOSE_CMD exec postgres pg_dump -U zeus zeus_content > "backups/zeus_backup_$timestamp.sql"
        echo -e "${GREEN}✅ Backup created: backups/zeus_backup_$timestamp.sql${NC}"
        ;;
    "restore")
        if [ -z "$2" ]; then
            echo -e "${RED}❌ Please specify backup file: ./docker-zeus.sh restore backup_file.sql${NC}"
            exit 1
        fi
        echo -e "${BLUE}📥 Restoring database from $2...${NC}"
        $COMPOSE_CMD exec -T postgres psql -U zeus zeus_content < "$2"
        echo -e "${GREEN}✅ Database restored${NC}"
        ;;
    "scale")
        replicas="${2:-2}"
        echo -e "${BLUE}📈 Scaling zeus-app to $replicas replicas...${NC}"
        $COMPOSE_CMD up -d --scale zeus-app="$replicas"
        echo -e "${GREEN}✅ Scaled to $replicas instances${NC}"
        ;;
    "monitor")
        echo -e "${BLUE}📊 Opening monitoring dashboard...${NC}"
        if command -v xdg-open &> /dev/null; then
            xdg-open http://localhost:8080
        elif command -v open &> /dev/null; then
            open http://localhost:8080
        else
            echo "Monitor dashboard: http://localhost:8080"
        fi
        ;;
    "clean")
        echo -e "${YELLOW}🧹 Cleaning up Docker resources...${NC}"
        $COMPOSE_CMD down -v --remove-orphans
        docker system prune -f
        echo -e "${GREEN}✅ Cleanup complete${NC}"
        ;;
    "update")
        echo -e "${BLUE}🔄 Updating Zeus Framework...${NC}"
        git pull
        $COMPOSE_CMD build --no-cache
        $COMPOSE_CMD up -d
        echo -e "${GREEN}✅ Update complete${NC}"
        ;;
    "health")
        echo -e "${BLUE}🏥 Health Check${NC}"
        echo "==============="
        
        # Check if containers are running
        if $COMPOSE_CMD ps | grep -q "Up"; then
            echo -e "${GREEN}✅ Containers: Running${NC}"
        else
            echo -e "${RED}❌ Containers: Not running${NC}"
        fi
        
        # Check database connection
        if $COMPOSE_CMD exec postgres pg_isready -U zeus >/dev/null 2>&1; then
            echo -e "${GREEN}✅ Database: Connected${NC}"
        else
            echo -e "${RED}❌ Database: Connection failed${NC}"
        fi
        
        # Check Redis
        if $COMPOSE_CMD exec redis redis-cli ping >/dev/null 2>&1; then
            echo -e "${GREEN}✅ Redis: Connected${NC}"
        else
            echo -e "${RED}❌ Redis: Connection failed${NC}"
        fi
        ;;
    "help"|*)
        echo "Usage: $0 {command} [options]"
        echo ""
        echo "🚀 Basic Commands:"
        echo "  build       - Build Docker images"
        echo "  up/start    - Start all services"
        echo "  stop        - Stop services (keep containers)"
        echo "  down        - Stop and remove containers"
        echo "  restart     - Restart all services"
        echo ""
        echo "🔍 Monitoring:"
        echo "  status      - Show container status"
        echo "  logs [svc]  - View logs (default: zeus-app)"
        echo "  monitor     - Open monitoring dashboard"
        echo "  health      - Run health checks"
        echo ""
        echo "🛠️ Development:"
        echo "  shell [svc] - Open bash shell (default: zeus-app)"
        echo "  db          - Connect to PostgreSQL"
        echo ""
        echo "📈 Scaling:"
        echo "  scale [N]   - Scale zeus-app to N replicas (default: 2)"
        echo ""
        echo "💾 Data Management:"
        echo "  backup      - Create database backup"
        echo "  restore <file> - Restore database from backup"
        echo ""
        echo "🧹 Maintenance:"
        echo "  clean       - Remove all containers and volumes"
        echo "  update      - Pull latest code and rebuild"
        echo ""
        exit 1
        ;;
esac