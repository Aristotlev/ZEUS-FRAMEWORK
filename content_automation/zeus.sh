#!/bin/bash

# Zeus Framework Launcher Script
# Handles environment setup and process management

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Project directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

echo -e "${BLUE}🏛️ Zeus Framework Launcher${NC}"
echo "=================================="

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${YELLOW}⚠️ .env file not found${NC}"
    echo "Copying .env.example to .env..."
    cp .env.example .env
    echo -e "${RED}❌ Please edit .env with your API keys before continuing${NC}"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}📦 Creating virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
echo -e "${BLUE}🔧 Activating virtual environment...${NC}"
source venv/bin/activate

# Install/update dependencies
echo -e "${BLUE}📚 Installing dependencies...${NC}"
pip install -r requirements.txt

# Check if this is first run
if [ ! -f ".setup_complete" ]; then
    echo -e "${BLUE}🔧 Running initial setup...${NC}"
    python setup.py
    
    if [ $? -eq 0 ]; then
        touch .setup_complete
        echo -e "${GREEN}✅ Initial setup complete${NC}"
    else
        echo -e "${RED}❌ Setup failed${NC}"
        exit 1
    fi
fi

# Parse command line arguments
case "${1:-run}" in
    "setup")
        echo -e "${BLUE}🔧 Running setup...${NC}"
        python setup.py
        ;;
    "run"|"start")
        echo -e "${GREEN}🚀 Starting Zeus Framework...${NC}"
        python main.py
        ;;
    "test")
        echo -e "${BLUE}🧪 Running tests...${NC}"
        python -m pytest tests/ -v
        ;;
    "monitor")
        echo -e "${BLUE}📊 Starting monitoring dashboard...${NC}"
        python monitor.py
        ;;
    "stop")
        echo -e "${YELLOW}🛑 Stopping Zeus Framework...${NC}"
        pkill -f "python main.py" || echo "No running instances found"
        ;;
    "logs")
        echo -e "${BLUE}📝 Showing logs...${NC}"
        tail -f logs/zeus_framework.log
        ;;
    "status")
        if pgrep -f "python main.py" > /dev/null; then
            echo -e "${GREEN}✅ Zeus Framework is running${NC}"
        else
            echo -e "${RED}❌ Zeus Framework is not running${NC}"
        fi
        ;;
    "clean")
        echo -e "${YELLOW}🧹 Cleaning temporary files...${NC}"
        rm -rf temp/* media_cache/* logs/*
        echo -e "${GREEN}✅ Cleanup complete${NC}"
        ;;
    "backup")
        echo -e "${BLUE}💾 Creating backup...${NC}"
        timestamp=$(date +"%Y%m%d_%H%M%S")
        mkdir -p backups
        pg_dump "$DATABASE_URL" > "backups/zeus_backup_$timestamp.sql"
        echo -e "${GREEN}✅ Database backup created: backups/zeus_backup_$timestamp.sql${NC}"
        ;;
    *)
        echo "Usage: $0 {setup|run|start|test|monitor|stop|logs|status|clean|backup}"
        echo ""
        echo "Commands:"
        echo "  setup   - Run initial setup and database initialization"
        echo "  run     - Start the Zeus Framework (default)"
        echo "  start   - Alias for run"  
        echo "  test    - Run test suite"
        echo "  monitor - Start monitoring dashboard"
        echo "  stop    - Stop running instances"
        echo "  logs    - Show live logs"
        echo "  status  - Check if Zeus is running"
        echo "  clean   - Clean temporary files"
        echo "  backup  - Backup database"
        exit 1
        ;;
esac