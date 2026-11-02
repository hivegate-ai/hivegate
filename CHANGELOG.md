# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.1] - 2026-10-31

First release on PyPI.

### Upgrading
- Apply the new migration, `db/migrations/011_token_usage_cost.sql`, or run `hivegate migrate`
  if you installed the package. It adds the usage metering columns to `token_usage`.
- `compose.yaml` now publishes ports on `127.0.0.1` only. Anything that connects to Postgres
  or Qdrant from another machine needs its own override.
- `compose.yaml` now reads `DB_PASS` and `DB_DATABASE` instead of `DB_PASSWORD` and `DB_NAME`,
  matching the app and `.env.example`. Postgres only reads them when a volume is first created.
- Major dependency upgrades: Pillow 13 and `anthropic` 2.3.0, plus `agno` 3.1.12.

### Added
- Install from PyPI with `uv tool install hivegate-ai` or `pipx install hivegate-ai`. The package
  is `hivegate-ai` because PyPI refuses `hivegate` as too similar to an existing project; the
  command is still `hivegate`, with `hivegate init`, `hivegate serve` and `hivegate migrate` (#133)
- Usage metering: the token cost of every model call is recorded and available from a read-only
  `/usage` API (#109)
- Prompt caching for Claude models (#110)
- `error_code: "provider_billing"` and a plain message when a provider account is out of credits,
  instead of the provider's raw error, on streaming routes (#125)
- `.env.example` documents `DEFAULT_CHAT_MODEL` (#76, @cestercian)
- A test that keeps `.env.example` in sync with `ApiSettings` (#103, @shashank1027)

### Changed
- `PROMPTS_SERVICE_ACCOUNT_KEY_PATH` sets the path to the prompts service account key (#133)
- `python-dotenv`, `requests` and `tenacity` are now declared dependencies, and `docker` and
  `kubernetes` are now extras (#133)

### Fixed
- `AGENT_DEBUG_MODE` is now honoured; it used to be ignored in favour of `TESTING`
  (#114, @ompatil1906)
- `build_mcp_toolkits` now warns when it drops an MCP server, naming the server and its transport
  (#146, @coughlmao)
- Team runs that pause for confirmation are now cached, so they can be approved and committed (#116)
- The quickstart now works from a fresh clone (#66)
- Migration `007_create_skills_table.sql` now quotes its reserved `references` column (#133)
- The comment on `cors_origin_list` now describes what its validator does (#101, @rathi-mohit)

### Security
- Upgraded dependencies to clear 155 open Dependabot alerts (#134)
- Bumped vulnerable packages in `uv.lock` (#79)

## [0.3.0] - 2026-09-29

### Renamed
- The project is now **HiveGate** (formerly Agents Gateway), to avoid confusion with
  the unrelated agentgateway.dev project. The repo moved to `hivegate-ai/hivegate`, the
  site to https://hivegate.dev, and the image to `ghcr.io/hivegate-ai/hivegate`.
  Upgrading an existing deployment:
  - The default database name in the deploy examples is now `hivegate`. Set
    `DB_DATABASE=agents_gateway` to keep using your existing database.
  - The default OpenTelemetry service name is now `hivegate`. Set
    `OTEL_SERVICE_NAME=agents-gateway` if your dashboards or alerts filter on the old name.
  - `GET /health` now returns `"service": "hivegate"`.
  - Pull `ghcr.io/hivegate-ai/hivegate` instead of `ghcr.io/liberzon/agents-gateway`.

### Added
- Open source community documentation (CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md)
- GitHub issue and PR templates
- Pre-commit hooks configuration
- Dependabot configuration for automated dependency updates
- CodeQL security scanning workflow
- Multi-cloud deployment examples (AWS, Azure, GCP, Kubernetes)
- Observability module with pluggable providers (Console, OTLP, Sentry, Logtail)
- Prompts storage and management service with postgres and langsmith backends
- Docker Compose configuration for local development
- Postman collection for API testing
- Testcontainers for integration testing
- Agent CLI and Team CLI tools
- V2 prompts API routes (`/v2/prompts`)
- Latest-of-a-tier model aliases (`anthropic:sonnet-latest`, `openai:terra-latest`, `google:flash-latest`, ...), resolved from each vendor's models API, cached daily, with a per-tier fallback; usable as a request `model` or as `DEFAULT_CHAT_MODEL`
- Native MCP tools on the chat path (#12)
- Claude 5 models (#48), and GPT-6, Gemini 3.5-3.8, Grok, Z.ai GLM and DeepSeek models (#57)
- A configurable default model, `DEFAULT_CHAT_MODEL` (#29)
- `GET /_/version` returns the commit the running process was built from (#56)

### Changed
- Renamed project from `agent-api` to `hivegate`
- **BREAKING**: Renamed `org_id` parameter to `tenant_id` across Knowledge API
- Database migrations consolidated into single `setup.sql`
- Version handling improvements in `pyproject.toml`
- Upgraded to agno 3.0.11 and mcp 2.2.0; MCP servers now connect through fastmcp (#54, #55)

### Removed
- ExpenseReimbursementToolkit and expense service
- Modal.py deployment configuration
- Redundant GCP deployment scripts
- Dependency review action from CI workflow

### Fixed
- Knowledge upload functionality
- Contacts search (removed organizations field)
- Various test suite fixes
- Claude safety refusals are now surfaced instead of streaming an empty reply (#49, #50)
- DB connection pool exhaustion: every `PostgresDb` now shares one engine (#37)
- The SSE streamer no longer string-concatenates dict content (#17)
- The chat handler fetches prompts from whichever prompt backend is configured (#4)
- `token_usage` column names now match the ORM (#5)

## [0.2.1] - 2026-01-20

Initial open source release.

### Added
- V2 API with full CRUD operations for agents, teams, and knowledge
- OAuth2 token management with encrypted storage
- Multi-provider toolkit support (Google, Microsoft)
- CalendarToolkit for calendar management
- EmailToolkit for email operations
- ContactsToolkit for contact management
- DriveToolkit for file operations
- Workspace Suite library for vendor-agnostic integrations
- Dynamic Knowledge Base with Qdrant vector storage
- PostgreSQL database for metadata persistence
- SQLite agent storage for conversation history
- Streaming responses via Server-Sent Events
- Token caching with TTL-based expiration
- Comprehensive test suite with pytest

### Security
- Fernet encryption for OAuth tokens
- Input validation with Pydantic models
- SQL injection prevention via SQLAlchemy ORM

[Unreleased]: https://github.com/hivegate-ai/hivegate/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/hivegate-ai/hivegate/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/hivegate-ai/hivegate/releases/tag/v0.3.0
[0.2.1]: https://github.com/hivegate-ai/hivegate/releases/tag/v0.2.1