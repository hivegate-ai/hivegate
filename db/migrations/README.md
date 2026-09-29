# Database Migrations

This directory contains the database setup script for hivegate.

## Database Setup

For new installations, run the unified setup script that creates all required tables:

### Using psql

```bash
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_DATABASE -f db/migrations/setup.sql
```

### Using the Python Script

```bash
python db/migrations/run_migration.py setup.sql
```

### Using Docker Compose

The `compose.yaml` automatically runs `setup.sql` on first startup via the init script mount.

## What setup.sql Creates

The setup script creates the following database objects:

### Schemas
- `public` - Core application tables
- `prompts` - Prompt templates (isolated)
- `ai` - Agno framework (tables auto-created at runtime)

### Tables (public schema)
| Table | Description |
|-------|-------------|
| `agent_info` | Agent metadata and configuration |
| `team_info` | Team metadata |
| `team_agent` | Team-agent relationships (junction table) |
| `knowledge_entries` | Knowledge base entries with JSONB metadata |
| `user_tokens` | Encrypted OAuth tokens and API keys |
| `token_usage` | API usage tracking |
| `api_keys` | API key authentication (hashed keys, scopes) |

### Tables (prompts schema)
| Table | Description |
|-------|-------------|
| `prompts` | Prompt templates |

### Shared Functions
- `update_updated_at_column()` - Trigger function for automatic `updated_at` timestamps

## Verifying Setup

After running the setup script, verify the database state:

```bash
python db/migrations/verify_migration.py
```

## Environment Variables

The migration scripts use these environment variables for database connection:

| Variable | Description |
|----------|-------------|
| `DB_HOST` | Database host |
| `DB_PORT` | Database port |
| `DB_USER` | Database user |
| `DB_PASS` | Database password |
| `DB_DATABASE` | Database name |

Alternatively, set `DATABASE_URL` for a complete connection string.
