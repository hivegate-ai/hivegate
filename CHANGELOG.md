# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Upgrading
- Run `hivegate migrate` (or apply `db/migrations/012_create_mcp_servers_table.sql`) to create
  the `mcp_servers` table. Header values are encrypted with `SECRET_TOKEN_ENC_KEY`, the key user
  tokens already use
- Validation errors (HTTP 422) no longer include `input`, on every route. FastAPI's default put
  the rejected value there, and for a missing field the whole request body, so a typo next to an
  access token or an MCP header sent the secret back in the error. `loc`, `msg` and `type` are
  unchanged. A client that read `input` out of a 422 needs to change
- `system_prompt` and `tools` on `POST /v2/agents/{agent_id}/chat` now take effect. They used to
  be accepted and ignored, so a client that already sends them will see them apply. An unknown
  toolkit name in `tools` is now a 400 (#88, @visveshkhanna)

### Fixed
- The console CLI (`cli/agent_cli.py`) now asks for approval on every call the gateway marks
  `requires_confirmation`, instead of only the tools in its own hardcoded list, so Tasks writes
  get a prompt. `run_claude_code` and `run_managed_agent` were never in that list, and their runs
  used to sit paused with no prompt; they now get the generic approval dialog too. The generic
  editor keeps object arguments (nested values and `null`s) by editing them as JSON, a paused
  batch is submitted once instead of being prompted again after every commit, and in streaming
  mode every pending call in the batch is prompted, not only the last one (#196)
- Chat agents now get agno's generated system prompt after their stored prompt: the current
  date and time in the agent's timezone, the list of attached skills, memory guidance, toolkit
  and knowledge-search instructions, and the markdown instruction. The stored prompt used to be
  passed as agno's `system_message`, which agno sends as-is, so all of that was dropped and
  `add_datetime_to_context` did nothing. `{var}` placeholders in stored prompts still resolve.
  A stored prompt that expected to be the whole system message will now have that context after
  it (#232, @visveshkhanna)
- A chat agent's MCP server with transport `HTTP` (any capitalisation other than lowercase)
  passed the chat check and was then dropped without its tools. Both now share one check
- An MCP server URL with an invalid port (`:99999`, `:abc`) is refused by the chat check instead
  of failing when chat connects

### Added
- MCP server registry: `/v2/mcp-servers` registers an HTTP MCP server once (id, URL, transport,
  optional static headers). Header values are encrypted at rest and responses list only their
  names (#91)
- Agents use registered MCP servers by id: `config.mcp_server_ids` on `POST /v2/agents`, or
  `PUT /v2/agents/{agent_id}/mcp-servers` for an existing agent. Chat connects them alongside
  any `worker_config.mcp_servers`, and resolves them on every message, so editing a server
  changes every agent that uses it. An unknown or deleted id is a 400; deleting a server an
  agent still uses is a 409 naming the agents. Supervisor and container workers keep
  `worker_config.mcp_servers` (#91)
- The knowledge embedder is configurable. `KNOWLEDGE_EMBEDDER=<provider>[:<model>]` picks any
  agno embedder (`openai:text-embedding-3-small`, `voyageai:voyage-3`, `ollama:nomic-embed-text`,
  `fastembed:BAAI/bge-small-en-v1.5`, ...), so knowledge ingestion and `search_knowledge_base` no
  longer need `GOOGLE_API_KEY` on a deployment that doesn't use Google. They need the embedder
  provider's credentials instead. `KNOWLEDGE_EMBEDDER_DIMENSIONS` sets the vector size where the
  provider's default is wrong for the model, and `KNOWLEDGE_COLLECTION` names the Qdrant
  collection. If an existing collection's vector size doesn't match the embedder, an error naming
  it is logged at startup and indexing and search are refused, instead of every upsert failing;
  chat keeps working. Each chunk records `embedding_model` and `embedding_dimensions`. Unset,
  behaviour is unchanged: `google:gemini-embedding-001` in `unified_knowledge` (#119,
  @visveshkhanna)

- A chat request's `system_prompt` is appended to the agent's stored prompt as extra
  instructions for that request. It never replaces the stored prompt, and `{...}` or `$...` in it
  can't pull session values such as `user_id` into the prompt (#88, @visveshkhanna)
- A chat request's `tools` (for example `["calendar", "email"]`) narrows the optional toolkits the
  agent gets. It can only remove toolkits the prompt's `available_tools:` tag enables, never add
  any. `[]` turns them all off, and the datetime resolver always stays on. A request with either
  field gets its own agent cache entry, and a `chat/commit` resuming that run sends the same
  values (#88, @visveshkhanna)

### Tests
- The two scripts in the repo root that `pytest tests/` never ran are gone (#142, @visveshkhanna).
  `test_current_knowledge_service.py` read an attribute that no longer exists, patched the CRUD
  functions after the service had already imported them, and swallowed every error, so it could
  not fail; `tests/test_knowledge_service.py` covers what it meant to. `test_reproduce_issue.py`
  needed a live server; the race it reproduced is now a unit test of the duplicate-key retry in
  `create_user_token`

## [0.4.1] - 2026-11-15

### Fixed
- Gemini works again with google-genai 3.0, which `pip install hivegate-ai==0.4.0` now
  resolves to. That release changed the client's default API version from `v1beta` to `v1`,
  where gemini-2.5 models refuse function calling (400) and preview models, including the
  default `gemini-3-flash-preview`, are not found (404). Every Gemini client HiveGate builds
  now pins `v1beta`: chat models, teams, the knowledge embedder, model listing and file
  uploads. Vertex AI (`GOOGLE_GENAI_USE_VERTEXAI=true`) keeps its own default, `v1beta1`.
  The 0.4.0 Docker image pins google-genai 2.34 and was not affected (#234)

## [0.4.0] - 2026-11-11

### Upgrading
- A model call that fails (a bad API key, an out-of-credits account, a provider outage) now
  returns **HTTP 502** with `status`, `error` and `detail` from non-streaming agent chat and team
  runs. It used to return 200 with the provider's raw error as the answer. Streaming routes still
  return 200, and now end with an SSE `error` event carrying the same fields. A client that read
  the error text out of a 200 reply needs to change (#205, @rajshadow)
- A chat agent configured with an MCP transport chat does not support (`stdio`, `sse`), or with
  an MCP URL that is not absolute HTTP or HTTPS, is now refused with HTTP 400. It used to run
  without those tools and say nothing. Supervisor and worker agents keep `stdio` (#206,
  @rayulumukku)

### Fixed
- `POST /v2/agents/{agent_id}/session/clear` now deletes the session's history. It used to return
  success and delete nothing (#161, @rajshadow)
- `docker compose up --wait` no longer fails on the Qdrant healthcheck, which is removed
  (#154, @Arun100203)

### Docs
- `DOCS_ENABLED` and `CORS_ORIGIN_LIST` are documented (#145, @yusufthecoder474)
- The Bright Data settings are documented (#147, @arpitkjaiswal)
- The PyPI summary and README title name HiveGate as an AI agent runtime (#163)

### Contributors
- Thanks to @Arun100203 and @coughlmao for the MCP OAuth spikes for GitHub (#139) and Atlassian
  (#141), and to everyone named above.

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

[Unreleased]: https://github.com/hivegate-ai/hivegate/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/hivegate-ai/hivegate/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/hivegate-ai/hivegate/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/hivegate-ai/hivegate/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/hivegate-ai/hivegate/releases/tag/v0.3.0
[0.2.1]: https://github.com/hivegate-ai/hivegate/releases/tag/v0.2.1