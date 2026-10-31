# HiveGate

**Run your Agno agents as a production API.**

[![HiveGate](https://hivegate.dev/assets/og-image.png)](https://hivegate.dev/?utm_source=github-readme)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![GitHub stars](https://img.shields.io/github/stars/hivegate-ai/hivegate?style=social)](https://github.com/hivegate-ai/hivegate/stargazers)
[![CI](https://github.com/hivegate-ai/hivegate/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/hivegate-ai/hivegate/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/hivegate-ai/hivegate)](https://github.com/hivegate-ai/hivegate/releases/latest)
[![Last commit](https://img.shields.io/github/last-commit/hivegate-ai/hivegate)](https://github.com/hivegate-ai/hivegate/commits/main)
[![Discussions](https://img.shields.io/github/discussions/hivegate-ai/hivegate)](https://github.com/hivegate-ai/hivegate/discussions)
[![Open Source Helpers](https://www.codetriage.com/hivegate-ai/hivegate/badges/users.svg)](https://www.codetriage.com/hivegate-ai/hivegate)

HiveGate is an open-source FastAPI service built on [Agno 3](https://github.com/agno-agi/agno). You write the agent, and HiveGate gives it what production needs:
- per-user sessions
- OAuth tokens for Google and Microsoft
- human approval for risky tool calls
- multi-agent teams with a supervisor
- versioned prompts
- token usage and cost metering
- tracing

**[Website](https://hivegate.dev/?utm_source=github-readme)** · **[Docs](https://hivegate.dev/docs/?utm_source=github-readme)** · **[Deploy guide](https://hivegate.dev/deploy/?utm_source=github-readme)** · **[Discussions](https://github.com/hivegate-ai/hivegate/discussions)** · **[Roadmap](https://github.com/hivegate-ai/hivegate/issues/72)**

```sh
git clone https://github.com/hivegate-ai/hivegate && cd hivegate
cp .env.example .env    # then set GOOGLE_API_KEY and uncomment AUTH_DISABLED=true
docker compose up -d    # Postgres + Qdrant, seeds demo agents
./scripts/dev_setup.sh && source .venv/bin/activate && ./scripts/start_server.sh
```

See the [Quickstart](#quickstart) for the full steps.

Or deploy the prebuilt image in one click:

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template?template=https://github.com/hivegate-ai/deploy)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/hivegate-ai/deploy)
[![Deploy to Koyeb](https://img.shields.io/badge/Deploy%20to-Koyeb-121212?style=for-the-badge&logo=koyeb&logoColor=white)](https://app.koyeb.com/deploy?type=git&repository=github.com/hivegate-ai/deploy)

> HiveGate is not a request-routing API gateway like Kong. It runs AI agents and exposes them over a REST API.

## Why HiveGate, and when you don't need it

[Agno](https://github.com/agno-agi/agno) gives you the agent itself: model calls, tools, memory and teams. Its own [AgentOS](https://docs.agno.com/agent-os/introduction) can also serve agents over HTTP, with sessions, human approval, RBAC and tracing. **If your agents are defined in code and AgentOS covers what you need, use AgentOS.**

HiveGate is for when your agents act *for your users*, in their own accounts, and are managed as data rather than code:

- **Per-user OAuth tokens.** Store each user's Google or Microsoft tokens once. HiveGate refreshes them, and the Gmail, Calendar, Contacts and Drive toolkits act as that user.
- **Approval before risky actions.** A tool call such as sending an email pauses the run until someone approves or denies it through the API.
- **Agents, prompts and skills as data.** Create and version them through the REST API, with no redeploy.
- **Per-tenant knowledge.** Documents are kept per tenant and per collection in Qdrant.
- **Supervisor teams with container workers.** Jobs run in Docker or Kubernetes, including Claude Code and managed-agent workers.
- **A ready-to-deploy service.** A prebuilt image with one-click deploys to Render, Railway and Koyeb, plus Kubernetes and cloud manifests.

## Features

- **Agent Management** - Create, configure, and manage AI agents via REST API
- **Team Orchestration** - Compose agents into teams for multi-agent workflows
- **Supervisor Platform** - Supervisor/worker execution with job queues, approval flows, and containerized runners (Docker & Kubernetes)
- **Skills & Evaluations** - Reusable skill definitions and a built-in evaluation framework for agent quality
- **Knowledge Base** - Store and index documents for agent retrieval (Qdrant)
- **Prompts Service** - Versioned prompt templates with pluggable storage (PostgreSQL, LangSmith)
- **Token Management** - Secure OAuth token storage with auto-refresh
- **Toolkits** - Pre-built integrations for Calendar, Email, Contacts, Drive (Google & Microsoft), plus Claude Code and managed-agent providers
- **Usage & Cost Metering** - Every model call recorded with its tokens and USD cost, queryable through the `/usage` API
- **Prompt Caching** - Claude models cache tools, the system prompt and the conversation, so repeat input bills at a fraction of the price
- **Observability** - Pluggable tracing and logging (OpenTelemetry, Sentry, OTLP)

## Quickstart

> Prerequisites: [Docker Desktop](https://www.docker.com/products/docker-desktop) installed and running, Python 3.11+.

### 1. Clone and configure

```sh
git clone https://github.com/hivegate-ai/hivegate
cd hivegate
cp .env.example .env
```

Edit `.env` **before starting the server**. The server reads it once at startup, so a key exported in another terminal won't reach it.

- Set at least one model provider key. The demo agents use Gemini by default, so set `GOOGLE_API_KEY`. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY`, `ZAI_API_KEY` and `DEEPSEEK_API_KEY` also work.
- For local use, uncomment `AUTH_DISABLED=true` so you can call the API without creating an API key first. Never enable it on a deployment other people can reach.

The database settings in `.env.example` already match the Postgres that Docker Compose starts.

### 2. Start

```sh
# Start PostgreSQL + Qdrant (seeds demo agents automatically)
docker compose up -d

# Set up the Python environment
./scripts/dev_setup.sh && source .venv/bin/activate

# Start the API server
./scripts/start_server.sh
```

### 3. Explore

```sh
# API docs (interactive)
open http://localhost:8000/docs

# List demo agents (works without an API key because of AUTH_DISABLED=true)
curl http://localhost:8000/v2/agents

# Get a specific agent
curl http://localhost:8000/v2/agents/demo-assistant
```

### 4. Chat with an agent

```sh
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

If the reply mentions an invalid API key, check the model key in `.env` and restart the server. To pick a different model, see [Choosing a model](#choosing-a-model).

### 5. Stop services

```sh
docker compose down        # Keep data
docker compose down -v     # Reset everything
```

## Project Structure

```
hivegate/
├── agents/                 # Model factory, model aliases, pricing
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
| Usage | `GET /usage/{live,summary,timeseries,calls,prices}` | Token usage and cost per agent, model and time bucket |

### Authentication

**V2 API** (`/v2/*`): API key via `X-API-Key` header
```bash
curl -H "X-API-Key: agw_xxxxx" http://localhost:8000/v2/agents
```

**Admin API** (`/admin/*`): Admin secret via `X-Admin-Secret` header
```bash
curl -H "X-Admin-Secret: your-secret" http://localhost:8000/admin/api-keys
```

**Usage API** (`/usage/*`): an API key with the `usage:read` scope, or the admin secret.
A key whose only scope is `usage:read` is refused on `/v2`, so a usage dashboard's key can't call agents.

**Development**: Set `AUTH_DISABLED=true` to bypass authentication.

## Choosing a model

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

Z.ai GLM models (`glm-5.3`, `glm-5.3-flashx`, `glm-5.3-flash`, `glm-5.2`) are pinned ids only - Z.ai
documents no model-listing endpoint to resolve an alias against. DeepSeek has two:
`deepseek-flash` (itself a moving name, now V4.1-Flash) and `deepseek-v4-pro`. OpenAI models run
on the Responses API: GPT-6 calls tools on Chat Completions only with reasoning off.

The gateway resolves an alias by listing the vendor's models through its SDK and
taking the newest one in the tier (`agents/model_resolver.py`), caches the answer
for a day, and falls back to a known model for the tier if the vendor can't be
asked. The resolution is logged (`Model alias anthropic:sonnet-latest -> ...`).
For example, `DEFAULT_CHAT_MODEL=anthropic:sonnet-latest`.

Claude models run with prompt caching on: the tools and system prompt are cached once per
agent, and the growing conversation is cached between round trips. Cached input bills at 0.1x
the input price (cache writes at 1.25x, 5-minute TTL).

## Usage and cost

Every chat, commit, MCP and team run - streamed or not, including failed, refused and
abandoned ones - writes a row to `token_usage` with its tokens (input, output, cache,
reasoning), round trips, tool calls, duration and `cost_usd`. Recording runs off the event
loop and never fails a call.

Costs come from the price table in `agents/pricing.py` (Anthropic, Gemini, OpenAI). A model
with no known price is reported as unpriced, never guessed; `/usage/summary` counts those in
`unpriced_calls` and `partially_priced_calls`. Add or correct a price without a redeploy:

```sh
PRICING_OVERRIDES='{"my-model": {"input": 5, "output": 25}}'   # USD per million tokens
```

```sh
curl -H "X-API-Key: agw_xxxxx" "http://localhost:8000/usage/summary?group_by=agent"
```

`scripts/usage_from_runs.py` builds a read-only per-agent cost report from Agno's stored runs.

## Toolkits

The `toolkits/` package provides Agno agent toolkits for external service integrations:

| Toolkit | Providers | Tools |
|---------|-----------|-------|
| CalendarToolkit | Google Calendar, Microsoft Calendar | schedule_meeting, list_events, cancel_meeting |
| EmailToolkit | Gmail, Outlook | send_email, search_emails, create_draft |
| ContactsToolkit | Google Contacts, Microsoft Contacts | create_contact, search_contacts, list_contacts |
| DriveToolkit | Google Drive, OneDrive | list_files, get_file_info, read_file |

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
| [Railway](https://railway.app/template?template=https://github.com/hivegate-ai/deploy) | `railway.toml` | `scripts/deploy_to_railway.sh` |
| [Render](https://render.com/deploy?repo=https://github.com/hivegate-ai/deploy) | `render.yaml` | `scripts/deploy_to_render.sh` |
| [Koyeb](https://app.koyeb.com/deploy?type=git&repository=github.com/hivegate-ai/deploy) | `koyeb.yaml` | `scripts/deploy_to_koyeb.sh` |

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
| `DOCS_ENABLED` | No | Default: `true`. Controls the OpenAPI documentation endpoints at `/docs` and `/redoc`; set to `false` to disable them. |
| `CORS_ORIGIN_LIST` | No | Default: `[]`. Additional allowed CORS origins. `http://localhost` and `http://localhost:3000` are always added to the configured origins, with duplicates removed. |
| `QDRANT_URL` | No | Qdrant vector database URL |
| `SECRET_TOKEN_ENC_KEY` | For OAuth tokens | Fernet key that encrypts stored OAuth tokens. Storing a token fails without it. Generate one with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` |
| `PRICING_OVERRIDES` | No | JSON of per-model prices for [usage metering](#usage-and-cost) |

\* At least one LLM API key is required.

See the [Observability](#observability) section for tracing/logging configuration.

## Database Schema

| Schema | Tables | Purpose |
|--------|--------|---------|
| `public` | agent_info, team_info, team_agent, knowledge_entries, user_tokens, api_keys, token_usage | Core data |
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

## Contributors

HiveGate is built in the open, and these people have contributed to it:

- [@cestercian](https://github.com/cestercian) — documented `DEFAULT_CHAT_MODEL` (#76), the
  project's first outside contribution
- [@rathi-mohit](https://github.com/rathi-mohit) — corrected the `cors_origin_list`
  documentation (#101)
- [@shashank1027](https://github.com/shashank1027) — added a test that keeps `.env.example`
  in sync with `ApiSettings` (#103)

There are [good first issues](https://github.com/hivegate-ai/hivegate/labels/good%20first%20issue)
open now, each small and self-contained. Comment on one and it's yours.

## Support

- [Agno Documentation](https://docs.agno.com)
- [Report an Issue](https://github.com/hivegate-ai/hivegate/issues)

## License

MIT License - see [LICENSE](LICENSE) for details.
