#!/bin/bash
# Deploy to Koyeb
# Prerequisites: Koyeb CLI installed (curl -fsSL https://cli.koyeb.com/install.sh | bash) and authenticated

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== Koyeb Deployment Script ===${NC}"

# Check if Koyeb CLI is installed
if ! command -v koyeb &> /dev/null; then
    echo -e "${RED}Error: Koyeb CLI is not installed${NC}"
    echo "Install it with: curl -fsSL https://cli.koyeb.com/install.sh | bash"
    echo "Then authenticate with: koyeb login"
    exit 1
fi

# Check if logged in
if ! koyeb organization list &> /dev/null 2>&1; then
    echo -e "${YELLOW}Not logged in to Koyeb. Please login:${NC}"
    koyeb login
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

# Get GitHub repository URL
echo -e "\n${YELLOW}GitHub Repository Configuration:${NC}"
prompt_env "GITHUB_REPO" "Enter GitHub repository (e.g., username/repo)"

# Create secrets in Koyeb
echo -e "\n${GREEN}Creating Koyeb secrets...${NC}"

create_secret_if_set() {
    local secret_name=$1
    local secret_value=$2
    if [ -n "$secret_value" ]; then
        echo "Creating secret: $secret_name"
        koyeb secrets create "$secret_name" --value="$secret_value" 2>/dev/null || \
        koyeb secrets update "$secret_name" --value="$secret_value" 2>/dev/null || true
    fi
}

# Core secrets
create_secret_if_set "db-host" "$DB_HOST"
create_secret_if_set "db-port" "$DB_PORT"
create_secret_if_set "db-user" "$DB_USER"
create_secret_if_set "db-pass" "$DB_PASS"
create_secret_if_set "db-database" "$DB_DATABASE"
create_secret_if_set "admin-secret" "$ADMIN_SECRET"

# LLM API keys
create_secret_if_set "google-api-key" "$GOOGLE_API_KEY"
create_secret_if_set "openai-api-key" "$OPENAI_API_KEY"
create_secret_if_set "anthropic-api-key" "$ANTHROPIC_API_KEY"

# Optional secrets
create_secret_if_set "qdrant-url" "$QDRANT_URL"
create_secret_if_set "qdrant-api-key" "$QDRANT_API_KEY"
create_secret_if_set "secret-token-enc-key" "$SECRET_TOKEN_ENC_KEY"

# Prompts storage secrets
create_secret_if_set "langchain-api-key" "$LANGCHAIN_API_KEY"
create_secret_if_set "service-prompts" "$SERVICE_PROMPTS"

# Observability secrets
create_secret_if_set "otel-environment" "production"
create_secret_if_set "sentry-dsn" "$SENTRY_DSN"
create_secret_if_set "sentry-traces-sample-rate" "$SENTRY_TRACES_SAMPLE_RATE"
create_secret_if_set "otel-otlp-endpoint" "$OTEL_OTLP_ENDPOINT"
create_secret_if_set "otel-jaeger-agent-host" "$OTEL_JAEGER_AGENT_HOST"
create_secret_if_set "otel-jaeger-agent-port" "$OTEL_JAEGER_AGENT_PORT"
create_secret_if_set "betterstack-source-token" "$BETTERSTACK_SOURCE_TOKEN"
create_secret_if_set "betterstack-host" "$BETTERSTACK_HOST"

# Build environment variables string for deployment
ENV_VARS=""
add_env() {
    local key=$1
    local secret=$2
    if [ -n "${!key}" ]; then
        ENV_VARS="$ENV_VARS --env $key=@$secret"
    fi
}

add_env "DB_HOST" "db-host"
add_env "DB_PORT" "db-port"
add_env "DB_USER" "db-user"
add_env "DB_PASS" "db-pass"
add_env "DB_DATABASE" "db-database"
add_env "ADMIN_SECRET" "admin-secret"
add_env "GOOGLE_API_KEY" "google-api-key"
add_env "OPENAI_API_KEY" "openai-api-key"
add_env "ANTHROPIC_API_KEY" "anthropic-api-key"
add_env "QDRANT_URL" "qdrant-url"
add_env "QDRANT_API_KEY" "qdrant-api-key"
add_env "SECRET_TOKEN_ENC_KEY" "secret-token-enc-key"

# Add prompts storage env vars
ENV_VARS="$ENV_VARS --env PROMPT_STORAGE_BACKEND=$PROMPT_STORAGE_BACKEND"
[ -n "$LANGCHAIN_API_KEY" ] && ENV_VARS="$ENV_VARS --env LANGCHAIN_API_KEY=@langchain-api-key"
[ -n "$SERVICE_PROMPTS" ] && ENV_VARS="$ENV_VARS --env SERVICE_PROMPTS=@service-prompts"

# Add observability env vars (with defaults for non-secret values)
ENV_VARS="$ENV_VARS --env OTEL_SERVICE_NAME=hivegate"
ENV_VARS="$ENV_VARS --env OTEL_ENVIRONMENT=@otel-environment"
ENV_VARS="$ENV_VARS --env OTEL_TRACING_BACKEND=$OTEL_TRACING_BACKEND"
ENV_VARS="$ENV_VARS --env OTEL_LOGGING_BACKEND=$OTEL_LOGGING_BACKEND"
ENV_VARS="$ENV_VARS --env OTEL_LOG_LEVEL=$OTEL_LOG_LEVEL"
ENV_VARS="$ENV_VARS --env OTEL_TRACING_SAMPLE_RATE=1.0"

# Add backend-specific observability env vars
[ -n "$SENTRY_DSN" ] && ENV_VARS="$ENV_VARS --env SENTRY_DSN=@sentry-dsn"
[ -n "$SENTRY_TRACES_SAMPLE_RATE" ] && ENV_VARS="$ENV_VARS --env SENTRY_TRACES_SAMPLE_RATE=@sentry-traces-sample-rate"
[ -n "$OTEL_OTLP_ENDPOINT" ] && ENV_VARS="$ENV_VARS --env OTEL_OTLP_ENDPOINT=@otel-otlp-endpoint"
[ -n "$OTEL_JAEGER_AGENT_HOST" ] && ENV_VARS="$ENV_VARS --env OTEL_JAEGER_AGENT_HOST=@otel-jaeger-agent-host"
[ -n "$OTEL_JAEGER_AGENT_PORT" ] && ENV_VARS="$ENV_VARS --env OTEL_JAEGER_AGENT_PORT=@otel-jaeger-agent-port"
[ -n "$BETTERSTACK_SOURCE_TOKEN" ] && ENV_VARS="$ENV_VARS --env BETTERSTACK_SOURCE_TOKEN=@betterstack-source-token"
[ -n "$BETTERSTACK_HOST" ] && ENV_VARS="$ENV_VARS --env BETTERSTACK_HOST=@betterstack-host"

# Deploy to Koyeb
echo -e "\n${GREEN}Deploying to Koyeb...${NC}"

SERVICE_NAME="hivegate"

# Check if service exists
if koyeb services get "$SERVICE_NAME" &> /dev/null; then
    echo "Updating existing service..."
    koyeb services update "$SERVICE_NAME" \
        --git "github.com/$GITHUB_REPO" \
        --git-branch main \
        --git-build-command "" \
        --git-run-command "uvicorn api.main:app --host 0.0.0.0 --port 8000" \
        --instance-type nano \
        --ports 8000:http \
        --routes /:8000 \
        --checks 8000:http:/health \
        --min-scale 1 \
        --max-scale 3 \
        $ENV_VARS
else
    echo "Creating new service..."
    koyeb services create "$SERVICE_NAME" \
        --git "github.com/$GITHUB_REPO" \
        --git-branch main \
        --git-build-command "" \
        --git-run-command "uvicorn api.main:app --host 0.0.0.0 --port 8000" \
        --instance-type nano \
        --ports 8000:http \
        --routes /:8000 \
        --checks 8000:http:/health \
        --min-scale 1 \
        --max-scale 3 \
        $ENV_VARS
fi

echo -e "\n${GREEN}=== Deployment initiated ===${NC}"
echo -e "Check status with: koyeb services get $SERVICE_NAME"
echo -e "View logs with: koyeb services logs $SERVICE_NAME"
echo -e "Open dashboard: https://app.koyeb.com"
