# Agents Gateway

**📖 Docs & landing page: [agentsgateway.dev](https://agentsgateway.dev)**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template?template=https://github.com/liberzon/agents-gateway-deploy)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/liberzon/agents-gateway-deploy)
[![Deploy to Koyeb](https://img.shields.io/badge/Deploy%20to-Koyeb-121212?style=for-the-badge&logo=koyeb&logoColor=white)](https://app.koyeb.com/deploy?type=git&repository=github.com/liberzon/agents-gateway-deploy)

A production-ready API gateway for serving AI agents. Built with FastAPI and [Agno 2.5.16+](https://github.com/agno-ai/agno).

## Features

- **Agent Management** - Create, configure, and manage AI agents via REST API
- **Team Orchestration** - Compose agents into teams for multi-agent workflows
- **Supervisor Platform** - Supervisor/worker execution with job queues, approval flows, and containerized runners (Docker & Kubernetes)
- **Skills & Evaluations** - Reusable skill definitions and a built-in evaluation framework for agent quality
- **Knowledge Base** - Store and index documents for agent retrieval (Qdrant)
- **Prompts Service** - Versioned prompt templates with pluggable storage (PostgreSQL, LangSmith)
- **Token Management** - Secure OAuth token storage with auto-refresh
- **Toolkits** - Pre-built integrations for Calendar, Email, Contacts, Drive (Google & Microsoft), plus Claude Code and managed-agent providers
- **Observability** - Pluggable tracing and logging (OpenTelemetry, Sentry, OTLP)

## Quickstart

> Prerequisites: [Docker Desktop](https://www.docker.com/products/docker-desktop) installed and running, Python 3.11+.

### 1. Clone and start

```sh
git clone <repository-url>
cd agents-gateway

# Start PostgreSQL + Qdrant (seeds demo agents automatically)
docker compose up -d

# Set up Python environment
./scripts/dev_setup.sh && source .venv/bin/activate

# Start the API server
./scripts/start_server.sh
```

### 2. Explore

```sh
# API docs (interactive)
open http://localhost:8000/docs

# List demo agents (no API key needed with AUTH_DISABLED=true)
curl http://localhost:8000/v2/agents

# Get a specific agent
curl http://localhost:8000/v2/agents/demo-assistant
```

### 3. Chat with an agent

Set at least one model provider API key, then chat:

```sh
export GOOGLE_API_KEY="your-google-api-key"       # Gemini (default)
# export OPENAI_API_KEY="your-openai-api-key"     # OpenAI (GPT)
# export ANTHROPIC_API_KEY="your-anthropic-api-key"  # Anthropic (Claude)
# export XAI_API_KEY="your-xai-api-key"              # xAI (Grok)
# export ZAI_API_KEY="your-zai-api-key"              # Z.ai (GLM)
# export DEEPSEEK_API_KEY="your-deepseek-api-key"    # DeepSeek

curl -X POST http://localhost:8000/v2/agents/demo-assistant/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What can you help me with?",
    "user_id": "user1",
    "session_id": "session1",
    "timezone": "UTC",
    "locale": "en",
    "stream": false
  }'
```

#### Choosing a model

A chat request may name a `model`; otherwise the gateway uses `DEFAULT_CHAT_MODEL`
(env), else `gemini-3-flash-preview`. Besides pinned ids (`claude-sonnet-4-6`,
`gpt-5.4`, ...), `model` accepts a **latest-of-a-tier alias** that follows the vendor's
newest model in that tier without a code change, and never moves to a pricier tier:

| Vendor | Aliases |
|---|---|
| Anthropic | `anthropic:haiku-latest`, `anthropic:sonnet-latest`, `anthropic:opus-latest` |
| OpenAI | `openai:luna-latest`, `openai:terra-latest`, `openai:sol-latest`, `openai:astra-latest` |
| Google | `google:flash-lite-latest`, `google:flash-latest`, `google:pro-latest` |
| xAI | `xai:grok-latest` |

Z.ai GLM models (`glm-5.3`, `glm-5.3-flash`, ...) are pinned ids only - Z.ai
documents no model-listing endpoint to resolve an alias against. DeepSeek has two:
`deepseek-flash` (itself a moving name, now V4.1-Flash) and `deepseek-v4-pro`. OpenAI models run
on the Responses API: GPT-6 calls tools on Chat Completions only with reasoning off.

The gateway resolves an alias by listing the vendor's models through its SDK and
taking the newest one in the tier (`agents/model_resolver.py`), caches the answer
for a day, and falls back to a known model for the tier if the vendor can't be
asked. The resolution is logged (`Model alias anthropic:sonnet-latest -> ...`).
For example, `DEFAULT_CHAT_MODEL=anthropic:sonnet-latest`.

### 4. Stop services

```sh
docker compose down        # Keep data
docker compose down -v     # Reset everything
```

## Project Structure

```
agents-gateway/
├── api/                    # FastAPI application
│   ├── routes/v2/          # V2 API endpoints (agents, teams, knowledge, tokens,
│   │                       #   prompts, skills, approvals, engines, targets)
│   ├── services/           # Shared services (auth, logging, knowledge)
│   └── observability/      # Tracing and logging providers
├── supervisor/             # Supervisor/worker orchestration
│   ├── queue/              # Job queue (producer, consumer, CRUD)
│   ├── pack/               # Agent pack loader/exporter
│   └── plugins/            # Plugin generator
├── remote_agent/           # Containerized worker runner (Docker & K8s runtimes)
├── prompts/                # Prompts service (parser, service, storage backends)
├── toolkits/               # Agno agent toolkits (Calendar, Email, Contacts,
│                           #   Drive, Claude Code, managed agents)
├── workspace_suite/        # Vendor-agnostic workspace integrations (Google, Microsoft)
├── evals/                  # Agent evaluation framework
├── db/                     # Database models and migrations
├── deploy/                 # Deployment manifests (aws, azure, gcp, generic)
└── scripts/                # Development and deployment scripts
```

## API Overview

All endpoints are documented at `/docs`. Key endpoints:

| Resource | Endpoint | Description |
|----------|----------|-------------|
| Agents | `GET/POST /v2/agents` | List and create agents |
| Agent Chat | `POST /v2/agents/{id}/chat` | Chat with an agent |
| Teams | `GET/POST /v2/teams` | List and create teams |
| Team Run | `POST /v2/teams/{id}/runs` | Execute a team |
| Knowledge | `GET/POST /v2/knowledge/{tenant_id}` | Manage knowledge entries |
| Tokens | `GET/POST /v2/users/{user_id}/tokens` | Manage OAuth tokens |
| Prompts | `GET/POST /v2/prompts` | Manage prompt templates |
| Skills | `GET/POST /v2/skills` | Manage reusable skill definitions |
| Engines | `GET/POST /v2/engines` | Manage supervisor execution engines |
| Targets | `GET/POST /v2/targets` | Manage supervisor run targets |
| Approvals | `GET/POST /v2/approvals` | Review and decide on pending job approvals |

### Authentication

**V2 API** (`/v2/*`): API key via `X-API-Key` header
```bash
curl -H "X-API-Key: agw_xxxxx" http://localhost:8000/v2/agents
```

**Admin API** (`/admin/*`): Admin secret via `X-Admin-Secret` header
```bash
curl -H "X-Admin-Secret: your-secret" http://localhost:8000/admin/api-keys
```

**Development**: Set `AUTH_DISABLED=true` to bypass authentication.

## Toolkits

The `toolkits/` package provides Agno agent toolkits for external service integrations:

| Toolkit | Providers | Tools |
|---------|-----------|-------|
| CalendarToolkit | Google Calendar, Microsoft Calendar | schedule_meeting, list_events, cancel_meeting |
| EmailToolkit | Gmail, Outlook | send_email, search_emails, create_draft |
| ContactsToolkit | Google Contacts, Microsoft Contacts | create_contact, search_contacts, list_contacts |
| DriveToolkit | Google Drive, OneDrive | list_files, read_file, upload_file |

All toolkits support:
- Confirmation-based workflow (user reviews before execution)
- OAuth token management with auto-refresh
- Graceful authentication fallback

See [`toolkits/README.md`](toolkits/README.md) for detailed documentation.

## Development

### Setup

```sh
# Install uv (package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment and install dependencies
./scripts/dev_setup.sh
source .venv/bin/activate
```

### Code Quality

```sh
# Run validation (format, lint, type check)
./scripts/run_validate.sh

# Run tests
pytest tests/v2/
```

### Dependencies

```sh
# Edit pyproject.toml, then regenerate requirements.txt
./scripts/generate_requirements.sh

# Upgrade all dependencies
./scripts/generate_requirements.sh upgrade
```

## Deployment

### One-Click Deploy

| Platform | Configuration | Script |
|----------|---------------|--------|
| [Railway](https://railway.app/template?template=https://github.com/liberzon/agents-gateway-deploy) | `railway.toml` | `scripts/deploy_to_railway.sh` |
| [Render](https://render.com/deploy?repo=https://github.com/liberzon/agents-gateway-deploy) | `render.yaml` | `scripts/deploy_to_render.sh` |
| [Koyeb](https://app.koyeb.com/deploy?type=git&repository=github.com/liberzon/agents-gateway-deploy) | `koyeb.yaml` | `scripts/deploy_to_koyeb.sh` |

### Cloud Platforms

Platform-specific deployment manifests and guides live under `deploy/`:

| Target | Path |
|--------|------|
| AWS (ECS + CloudFormation) | [`deploy/aws/`](deploy/aws/) |
| Azure (Container Apps + Bicep) | [`deploy/azure/`](deploy/azure/) |
| Google Cloud Run | [`deploy/gcp/`](deploy/gcp/) |
| Generic (docker-compose, Kubernetes) | [`deploy/generic/`](deploy/generic/) |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASS`, `DB_DATABASE` | Yes | PostgreSQL connection |
| `ADMIN_SECRET` | Yes (prod) | Admin endpoint authentication |
| `GOOGLE_API_KEY` | * | Google/Gemini API key |
| `OPENAI_API_KEY` | * | OpenAI API key |
| `ANTHROPIC_API_KEY` | * | Anthropic API key |
| `XAI_API_KEY` | * | xAI API key (Grok models) |
| `ZAI_API_KEY` | * | Z.ai API key (GLM models) |
| `DEEPSEEK_API_KEY` | * | DeepSeek API key |
| `QDRANT_URL` | No | Qdrant vector database URL |
| `SECRET_TOKEN_ENC_KEY` | No | Token encryption key (auto-generated) |

\* At least one LLM API key is required.

See the [Observability](#observability) section for tracing/logging configuration.

## Database Schema

| Schema | Tables | Purpose |
|--------|--------|---------|
| `public` | agent_info, team_info, team_agent, knowledge_entries, user_tokens, api_keys | Core data |
| `prompts` | prompts | Prompt templates |
| `ai` | (auto-created) | Agno agent sessions & memories |

### Setup

```bash
# Run migrations (auto-runs on first docker compose up)
psql -h $DB_HOST -U $DB_USER -d $DB_DATABASE -f db/migrations/setup.sql
```

## Observability

Pluggable tracing and logging using OpenTelemetry:

| Variable | Default | Options |
|----------|---------|---------|
| `OTEL_TRACING_BACKEND` | `console` | `console`, `otlp`, `sentry` |
| `OTEL_LOGGING_BACKEND` | `console` | `console`, `otlp`, `logtail` |

**Example: Production with Sentry**
```sh
OTEL_TRACING_BACKEND=sentry
SENTRY_DSN=https://xxx@sentry.io/xxx
```

**Example: Cloud-native with OTLP**
```sh
OTEL_TRACING_BACKEND=otlp
OTEL_LOGGING_BACKEND=otlp
OTEL_OTLP_ENDPOINT=http://collector:4317
```

## Support

- [Agno Documentation](https://docs.agno.com)
- [Report an Issue](https://github.com/liberzon/agents-gateway/issues)

## License

MIT License - see [LICENSE](LICENSE) for details.
