#!/bin/bash
# Deploy to Railway
# Prerequisites: Railway CLI installed (npm i -g @railway/cli) and authenticated (railway login)

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Railway Deployment Script ===${NC}"

# Check if Railway CLI is installed
if ! command -v railway &> /dev/null; then
    echo -e "${RED}Error: Railway CLI is not installed${NC}"
    echo "Install it with: npm i -g @railway/cli"
    echo "Then authenticate with: railway login"
    exit 1
fi

# Check if logged in
if ! railway whoami &> /dev/null; then
    echo -e "${YELLOW}Not logged in to Railway. Please login:${NC}"
    railway login
fi

# Load environment variables from .env if it exists
if [ -f .env ]; then
    echo -e "${GREEN}Loading environment variables from .env${NC}"
    set -a
    source .env
    set +a
fi

# Function to prompt for environment variable
prompt_env() {
    local var_name=$1
    local prompt_text=$2
    local is_secret=${3:-false}

    if [ -z "${!var_name}" ]; then
        if [ "$is_secret" = true ]; then
            read -sp "$prompt_text: " value
            echo
        else
            read -p "$prompt_text: " value
        fi
        export "$var_name=$value"
    fi
}

echo -e "${YELLOW}Checking required environment variables...${NC}"

# Required variables
prompt_env "DB_HOST" "Enter database host"
prompt_env "DB_PORT" "Enter database port (default: 5432)"
DB_PORT=${DB_PORT:-5432}
prompt_env "DB_USER" "Enter database user"
prompt_env "DB_PASS" "Enter database password" true
prompt_env "DB_DATABASE" "Enter database name"
prompt_env "ADMIN_SECRET" "Enter admin secret" true

# LLM API Keys (at least one required)
echo -e "\n${YELLOW}LLM API Keys (at least one required):${NC}"
prompt_env "GOOGLE_API_KEY" "Enter Google API key (press Enter to skip)" true
prompt_env "OPENAI_API_KEY" "Enter OpenAI API key (press Enter to skip)" true
prompt_env "ANTHROPIC_API_KEY" "Enter Anthropic API key (press Enter to skip)" true

# Check at least one LLM key is set
if [ -z "$GOOGLE_API_KEY" ] && [ -z "$OPENAI_API_KEY" ] && [ -z "$ANTHROPIC_API_KEY" ]; then
    echo -e "${RED}Error: At least one LLM API key is required${NC}"
    exit 1
fi

# Optional variables
echo -e "\n${YELLOW}Optional variables (press Enter to skip):${NC}"
prompt_env "QDRANT_URL" "Enter Qdrant URL"
prompt_env "QDRANT_API_KEY" "Enter Qdrant API key" true
prompt_env "SECRET_TOKEN_ENC_KEY" "Enter token encryption key" true

# Prompts storage configuration
echo -e "\n${BLUE}=== Prompts Storage Configuration ===${NC}"
echo "Backends: postgres (default), langsmith, service"
echo ""

prompt_env "PROMPT_STORAGE_BACKEND" "Enter prompts backend (default: postgres)"
PROMPT_STORAGE_BACKEND=${PROMPT_STORAGE_BACKEND:-postgres}

if [[ "$PROMPT_STORAGE_BACKEND" == "langsmith" ]]; then
    prompt_env "LANGCHAIN_API_KEY" "Enter LangChain API key" true
fi

if [[ "$PROMPT_STORAGE_BACKEND" == "service" ]]; then
    prompt_env "SERVICE_PROMPTS" "Enter external prompts service URL"
fi

# Observability configuration
echo -e "\n${BLUE}=== Observability Configuration ===${NC}"
echo "Tracing backends: console, otlp, jaeger, sentry"
echo "Logging backends: console, otlp, logtail (comma-separated)"
echo ""

prompt_env "OTEL_TRACING_BACKEND" "Enter tracing backend (default: console)"
OTEL_TRACING_BACKEND=${OTEL_TRACING_BACKEND:-console}

prompt_env "OTEL_LOGGING_BACKEND" "Enter logging backend (default: console)"
OTEL_LOGGING_BACKEND=${OTEL_LOGGING_BACKEND:-console}

prompt_env "OTEL_LOG_LEVEL" "Enter log level (default: INFO)"
OTEL_LOG_LEVEL=${OTEL_LOG_LEVEL:-INFO}

# Backend-specific configuration
if [[ "$OTEL_TRACING_BACKEND" == "sentry" ]] || [[ "$OTEL_LOGGING_BACKEND" == *"sentry"* ]]; then
    echo -e "\n${YELLOW}Sentry Configuration:${NC}"
    prompt_env "SENTRY_DSN" "Enter Sentry DSN" true
    prompt_env "SENTRY_TRACES_SAMPLE_RATE" "Enter traces sample rate (default: 1.0)"
    SENTRY_TRACES_SAMPLE_RATE=${SENTRY_TRACES_SAMPLE_RATE:-1.0}
fi

if [[ "$OTEL_TRACING_BACKEND" == "otlp" ]] || [[ "$OTEL_LOGGING_BACKEND" == *"otlp"* ]]; then
    echo -e "\n${YELLOW}OTLP Configuration:${NC}"
    prompt_env "OTEL_OTLP_ENDPOINT" "Enter OTLP endpoint (e.g., http://collector:4317)"
fi

if [[ "$OTEL_TRACING_BACKEND" == "jaeger" ]]; then
    echo -e "\n${YELLOW}Jaeger Configuration:${NC}"
    prompt_env "OTEL_JAEGER_AGENT_HOST" "Enter Jaeger agent host"
    prompt_env "OTEL_JAEGER_AGENT_PORT" "Enter Jaeger agent port (default: 6831)"
    OTEL_JAEGER_AGENT_PORT=${OTEL_JAEGER_AGENT_PORT:-6831}
fi

if [[ "$OTEL_LOGGING_BACKEND" == *"logtail"* ]]; then
    echo -e "\n${YELLOW}Better Stack / Logtail Configuration:${NC}"
    prompt_env "BETTERSTACK_SOURCE_TOKEN" "Enter Better Stack source token" true
    prompt_env "BETTERSTACK_HOST" "Enter Better Stack host URL"
fi

# Initialize Railway project if not already initialized
if [ ! -f ".railway" ]; then
    echo -e "\n${GREEN}Initializing Railway project...${NC}"
    railway init
fi

# Link to existing project or create new one
echo -e "\n${GREEN}Linking to Railway project...${NC}"
railway link

# Set environment variables
echo -e "\n${GREEN}Setting environment variables...${NC}"

# Core variables
railway variables set DB_HOST="$DB_HOST"
railway variables set DB_PORT="$DB_PORT"
railway variables set DB_USER="$DB_USER"
railway variables set DB_PASS="$DB_PASS"
railway variables set DB_DATABASE="$DB_DATABASE"
railway variables set ADMIN_SECRET="$ADMIN_SECRET"

# Set LLM keys if provided
[ -n "$GOOGLE_API_KEY" ] && railway variables set GOOGLE_API_KEY="$GOOGLE_API_KEY"
[ -n "$OPENAI_API_KEY" ] && railway variables set OPENAI_API_KEY="$OPENAI_API_KEY"
[ -n "$ANTHROPIC_API_KEY" ] && railway variables set ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY"

# Set optional variables if provided
[ -n "$QDRANT_URL" ] && railway variables set QDRANT_URL="$QDRANT_URL"
[ -n "$QDRANT_API_KEY" ] && railway variables set QDRANT_API_KEY="$QDRANT_API_KEY"
[ -n "$SECRET_TOKEN_ENC_KEY" ] && railway variables set SECRET_TOKEN_ENC_KEY="$SECRET_TOKEN_ENC_KEY"

# Prompts storage variables
railway variables set PROMPT_STORAGE_BACKEND="$PROMPT_STORAGE_BACKEND"
[ -n "$LANGCHAIN_API_KEY" ] && railway variables set LANGCHAIN_API_KEY="$LANGCHAIN_API_KEY"
[ -n "$SERVICE_PROMPTS" ] && railway variables set SERVICE_PROMPTS="$SERVICE_PROMPTS"

# Observability variables
railway variables set OTEL_SERVICE_NAME="hivegate"
railway variables set OTEL_ENVIRONMENT="production"
railway variables set OTEL_TRACING_BACKEND="$OTEL_TRACING_BACKEND"
railway variables set OTEL_LOGGING_BACKEND="$OTEL_LOGGING_BACKEND"
railway variables set OTEL_LOG_LEVEL="$OTEL_LOG_LEVEL"
railway variables set OTEL_TRACING_SAMPLE_RATE="1.0"

# Backend-specific observability variables
[ -n "$SENTRY_DSN" ] && railway variables set SENTRY_DSN="$SENTRY_DSN"
[ -n "$SENTRY_TRACES_SAMPLE_RATE" ] && railway variables set SENTRY_TRACES_SAMPLE_RATE="$SENTRY_TRACES_SAMPLE_RATE"
[ -n "$OTEL_OTLP_ENDPOINT" ] && railway variables set OTEL_OTLP_ENDPOINT="$OTEL_OTLP_ENDPOINT"
[ -n "$OTEL_JAEGER_AGENT_HOST" ] && railway variables set OTEL_JAEGER_AGENT_HOST="$OTEL_JAEGER_AGENT_HOST"
[ -n "$OTEL_JAEGER_AGENT_PORT" ] && railway variables set OTEL_JAEGER_AGENT_PORT="$OTEL_JAEGER_AGENT_PORT"
[ -n "$BETTERSTACK_SOURCE_TOKEN" ] && railway variables set BETTERSTACK_SOURCE_TOKEN="$BETTERSTACK_SOURCE_TOKEN"
[ -n "$BETTERSTACK_HOST" ] && railway variables set BETTERSTACK_HOST="$BETTERSTACK_HOST"

# Deploy
echo -e "\n${GREEN}Deploying to Railway...${NC}"
railway up --detach

echo -e "\n${GREEN}=== Deployment initiated ===${NC}"
echo -e "Check status with: railway status"
echo -e "View logs with: railway logs"
echo -e "Open dashboard with: railway open"
