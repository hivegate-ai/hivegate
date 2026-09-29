# Scripts

This directory contains utility scripts for the hivegate project.

## JSONL Vector Import Tool

### Overview

`import_jsonl_vectors.py` - Import pre-computed Qdrant vectors from JSONL files into the knowledge base.

This tool allows you to load JSONL files containing pre-computed vector embeddings directly into Qdrant, bypassing the standard embedding pipeline. Each JSONL line should contain a complete Qdrant point with vector and payload.

### Features

- ✅ Import pre-computed vectors from JSONL files
- ✅ Map user payload to FileChunk schema
- ✅ Preserve additional metadata fields from input
- ✅ **Dry-run mode** for validation without insertion
- ✅ **Verbose mode** for detailed object inspection
- ✅ Configurable Qdrant collection
- ✅ Batch insertion for efficiency
- ✅ Schema validation

### Usage

#### Basic Usage

```bash
python scripts/import_jsonl_vectors.py \
  --file data/vectors.jsonl \
  --org-id "123e4567-e89b-12d3-a456-426614174000" \
  --user-id "987fcdeb-51a2-43d7-9876-543210987654"
```

#### Dry-Run Mode (Validation Only)

```bash
python scripts/import_jsonl_vectors.py \
  --file data/vectors.jsonl \
  --org-id "123e4567-e89b-12d3-a456-426614174000" \
  --user-id "987fcdeb-51a2-43d7-9876-543210987654" \
  --dry-run --verbose
```

#### With All Options

```bash
python scripts/import_jsonl_vectors.py \
  --file data/vectors.jsonl \
  --org-id "123e4567-e89b-12d3-a456-426614174000" \
  --user-id "987fcdeb-51a2-43d7-9876-543210987654" \
  --project-id "550e8400-e29b-41d4-a716-446655440000" \
  --qdrant-url "http://localhost:6333" \
  --collection "unified_knowledge" \
  --batch-size 100 \
  --verbose
```

### CLI Arguments

#### Required Arguments

- `--file JSONL_FILE` - Path to input JSONL file
- `--org-id ORG_ID` - Organization UUID
- `--user-id USER_ID` - User UUID

#### Optional Arguments

- `--project-id PROJECT_ID` - Project UUID (nullable)
- `--qdrant-url URL` - Qdrant server URL (default: `http://localhost:6333`)
- `--collection NAME` - Collection name (default: `unified_knowledge`)
- `--batch-size N` - Batch size for insertion (default: 100)
- `--dry-run` - Validate only, do not insert to Qdrant
- `--verbose` - Print constructed objects (first 3 samples)

### Input JSONL Format

Each line in the JSONL file should be a valid JSON object with the following structure:

```json
{
  "id": "chunk_001",
  "vector": [0.1, 0.2, 0.3, ...],
  "payload": {
    "text": "Document content text",
    "startPosition": 0,
    "endPosition": 123,
    "chunkIndex": 0,
    "fileId": "550e8400-e29b-41d4-a716-446655440000",
    "fileName": "document.pdf",
    "embeddingModel": "text-embedding-004",
    "metadata": {
      "source": "documentation",
      "section": "introduction",
      "chunk_index": 0
    }
  }
}
```

### Output Schema

The tool maps the input JSONL to the FileChunk schema:

```json
{
  "name": "document.pdf",
  "meta_data": {
    "page": 0,
    "chunk": 0,
    "chunk_size": 123,
    "org_id": "123e4567-e89b-12d3-a456-426614174000",
    "project_id": null,
    "file_id": "550e8400-e29b-41d4-a716-446655440000",
    "original_filename": "document.pdf",
    "file_type": "imported",
    "content_type": "application/jsonl",
    "source_id": "550e8400-e29b-41d4-a716-446655440000",
    "user_id": "987fcdeb-51a2-43d7-9876-543210987654",
    "file_hash": "e8456cee7fd2348cfa239342b10a23bd",
    "file_size": 123,
    "embedding_model": "text-embedding-004",
    "start_position": 0,
    "end_position": 123,
    "source": "documentation",
    "section": "introduction",
    "chunk_index": 0
  },
  "content": "Document content text",
  "usage": null
}
```

### Field Mapping

| Input Field | Output Field | Source |
|-------------|--------------|--------|
| `id` | Point ID | JSONL |
| `vector` | Vector | JSONL |
| `payload.fileName` | `name` | JSONL |
| `payload.text` | `content` | JSONL |
| `payload.fileId` | `meta_data.file_id` | JSONL |
| `payload.fileId` | `meta_data.source_id` | JSONL |
| `payload.chunkIndex` | `meta_data.chunk` | JSONL |
| `payload.metadata.chunk_index` | `meta_data.page` | JSONL |
| - | `meta_data.org_id` | CLI |
| - | `meta_data.user_id` | CLI |
| - | `meta_data.project_id` | CLI |
| `payload.embeddingModel` | `meta_data.embedding_model` | JSONL (preserved) |
| `payload.startPosition` | `meta_data.start_position` | JSONL (preserved) |
| `payload.endPosition` | `meta_data.end_position` | JSONL (preserved) |
| `payload.metadata.*` | `meta_data.*` | JSONL (preserved) |

### Example Output (Dry-Run Mode)

```bash
$ python scripts/import_jsonl_vectors.py \
  --file scripts/sample_vectors.jsonl \
  --org-id "123e4567-e89b-12d3-a456-426614174000" \
  --user-id "987fcdeb-51a2-43d7-9876-543210987654" \
  --dry-run --verbose

2025-11-18 10:57:30,784 - INFO - Loaded schema: scripts/schemas/file_chunk_schema.json
2025-11-18 10:57:30,784 - INFO - Processing JSONL file: scripts/sample_vectors.jsonl
2025-11-18 10:57:30,786 - INFO -
--- Sample Point 1 (line 1) ---
2025-11-18 10:57:30,786 - INFO - ID: chunk_001
2025-11-18 10:57:30,786 - INFO - Vector dimensions: 5
2025-11-18 10:57:30,786 - INFO - Payload:
{
  "name": "ai_guide.pdf",
  "meta_data": {
    "page": 0,
    "chunk": 0,
    "chunk_size": 83,
    "org_id": "123e4567-e89b-12d3-a456-426614174000",
    "project_id": null,
    "file_id": "550e8400-e29b-41d4-a716-446655440000",
    ...
  },
  "content": "This is a sample document chunk...",
  "usage": null
}

============================================================
PROCESSING SUMMARY
============================================================
Total lines processed: 3
Valid points: 3
Invalid objects: 0

Vector dimensions: 5

============================================================
DRY RUN MODE - No insertion performed
============================================================
```

### Dependencies

The script requires the following Python packages:

- `jsonschema` - For schema validation
- `qdrant-client` - For Qdrant interaction

Install with:

```bash
uv pip install jsonschema qdrant-client
```

### Sample Data

A sample JSONL file is provided at `scripts/sample_vectors.jsonl` for testing.

### Schema File

The FileChunk JSON schema is located at `scripts/schemas/file_chunk_schema.json`.

### Error Handling

- Invalid JSON lines are logged and skipped
- Schema validation failures are reported with details
- Missing required fields (`fileId`, `vector`) cause line to be skipped
- Summary report shows valid/invalid counts

### Best Practices

1. **Always run with `--dry-run --verbose` first** to validate your data
2. **Check vector dimensions** match your collection's configuration
3. **Use appropriate batch size** (100-200 for most cases)
4. **Monitor logs** for validation errors
5. **Ensure UUIDs** are valid format for org_id, user_id, project_id, file_id

### Troubleshooting

**Schema Validation Failed**:
- Ensure all required fields are present in your JSONL
- Check that UUIDs are properly formatted
- Verify `meta_data.additionalProperties` is set to `true` in schema

**Collection Not Found**:
- Verify Qdrant server is running
- Check collection name matches existing collection
- Use `--collection` flag to specify correct name

**Module Not Found (jsonschema)**:
- Install missing dependencies: `uv pip install jsonschema`

---

## Mock Prompts Server

### Overview

`mock_prompts_server.py` - Development server with persistent caching for prompts service.

**Purpose**: Cache responses from the real prompts service to avoid network latency during local development and enable offline work.

### Quick Start

```bash
# 1. Set the real prompts service URL (one-time setup)
export SERVICE_PROMPTS_REAL="https://your-real-prompts-service.run.app"

# 2. Start the mock server
python scripts/mock_prompts_server.py

# 3. Configure your app to use the mock server
export SERVICE_PROMPTS=http://localhost:8001

# 4. Start your app as normal
uvicorn api.main:app --reload
```

### Features

- ✅ **Persistent disk caching** - Stores prompts to `.prompts_cache/` directory
- ✅ **Offline mode** - Works offline after initial cache warm-up
- ✅ **Cache warming** - Pre-fetch all prompts with `--warm-cache`
- ✅ **Force refresh** - Bypass cache with `?force_refresh=true` query param
- ✅ **Cache management** - View stats, clear cache, warm cache via API
- ✅ **Proxy mode** - Falls back to real service on cache miss
- ✅ **Health checks** - Built-in health endpoint

### Usage

```bash
# Start server (default port 8001)
python scripts/mock_prompts_server.py

# Custom port
python scripts/mock_prompts_server.py --port 8002

# Clear cache and start fresh
python scripts/mock_prompts_server.py --clear-cache

# Warm cache on startup (fetch all prompts)
python scripts/mock_prompts_server.py --warm-cache

# Debug mode
python scripts/mock_prompts_server.py --log-level DEBUG

# Custom host (for network access)
python scripts/mock_prompts_server.py --host 0.0.0.0
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | List all prompts (supports `?force_refresh=true`) |
| `/{prompt_name}` | GET | Get specific prompt (supports `?force_refresh=true`) |
| `/cache/stats` | GET | View cache statistics and coverage |
| `/cache/warm` | POST | Pre-fetch all prompts to cache |
| `/cache/clear` | POST | Clear all cached data |
| `/health` | GET | Health check and configuration status |

### Examples

```bash
# View cache statistics
curl http://localhost:8001/cache/stats

# Example output:
# {
#   "cache_directory": "/path/to/.prompts_cache",
#   "prompts_list_cached": true,
#   "total_prompts": 25,
#   "cached_prompts": 25,
#   "cache_coverage": "25/25",
#   "cache_hit_rate": "100.0%"
# }

# Warm up cache (fetch all prompts)
curl -X POST http://localhost:8001/cache/warm

# Get a prompt (from cache if available)
curl http://localhost:8001/assistant

# Force refresh from real service
curl "http://localhost:8001/assistant?force_refresh=true"

# List all prompts
curl http://localhost:8001/

# Clear cache
curl -X POST http://localhost:8001/cache/clear

# Health check
curl http://localhost:8001/health
```

### Typical Workflow

1. **First run**: Mock server proxies to real service and caches responses
   ```bash
   # Start mock server
   python scripts/mock_prompts_server.py

   # Warm cache (fetch all prompts)
   curl -X POST http://localhost:8001/cache/warm
   ```

2. **Development**: Mock server serves from cache (instant responses, no network)
   ```bash
   # Configure your app
   export SERVICE_PROMPTS=http://localhost:8001

   # Start your app
   uvicorn api.main:app --reload
   ```

3. **Updates needed**: Use `?force_refresh=true` or clear cache
   ```bash
   # Refresh specific prompt
   curl "http://localhost:8001/assistant?force_refresh=true"

   # Or clear entire cache
   curl -X POST http://localhost:8001/cache/clear
   ```

4. **Offline work**: Works from cache without network access
   ```bash
   # No SERVICE_PROMPTS_REAL needed if cache is warm
   python scripts/mock_prompts_server.py
   ```

### Cache Structure

**Cache Location**: `.prompts_cache/` (git-ignored)

```
.prompts_cache/
├── prompts_list.json          # List of all prompt names
└── prompts/
    ├── a1b2c3d4e5.json       # Individual prompt files
    ├── f6g7h8i9j0.json       # (hashed filenames for safety)
    └── ...
```

Each cached prompt file contains:
```json
{
  "prompt_name": "assistant",
  "data": {
    "id": "assistant",
    "template": "You are a helpful assistant...",
    "variables": ["user_context", "question"],
    ...
  }
}
```

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `SERVICE_PROMPTS_REAL` | Real prompts service URL | Yes (for proxy mode) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path to service account key | Optional (defaults to `agents/service-account.json`) |
| `SERVICE_PROMPTS` | Your app should point to mock server | Yes (in your app) |

**Example `.env` configuration**:
```bash
# For mock server (to proxy to real service)
SERVICE_PROMPTS_REAL=https://dev-prompts-service-xyz.run.app

# For your app (to use mock server)
SERVICE_PROMPTS=http://localhost:8001
```

### Benefits

- ⚡ **10-100x faster** local development (no network calls)
- 🔌 **Offline development** after initial cache
- 💰 **Reduced costs** (fewer Cloud Run invocations)
- 🎯 **Consistent testing** (stable cached data)
- 🚀 **Faster startup** (no waiting for prompts service)
- 📊 **Cache monitoring** (stats and coverage tracking)

### Performance Comparison

| Operation | Without Mock | With Mock (cached) | Speedup |
|-----------|--------------|-------------------|---------|
| Get single prompt | ~200-500ms | ~1-5ms | **100x** |
| Load all prompts (30) | ~7-8 seconds | ~30-150ms | **50x** |
| App startup | ~20 seconds | ~5 seconds | **4x** |

### Troubleshooting

**Error: "Real prompts service URL not configured"**
- Set `SERVICE_PROMPTS_REAL` environment variable
- Or use `--warm-cache` first with real service configured

**Error: "Service account key not found"**
- Set `GOOGLE_APPLICATION_CREDENTIALS` environment variable
- Or ensure `agents/service-account.json` exists

**Cache not updating**
- Use `?force_refresh=true` query parameter
- Or clear cache: `curl -X POST http://localhost:8001/cache/clear`

**Port already in use**
- Use `--port` flag: `python scripts/mock_prompts_server.py --port 8002`
- Or kill existing process: `lsof -ti:8001 | xargs kill`

### Best Practices

1. **Warm cache before offline work**
   ```bash
   curl -X POST http://localhost:8001/cache/warm
   ```

2. **Check cache stats regularly**
   ```bash
   curl http://localhost:8001/cache/stats
   ```

3. **Force refresh after prompts service updates**
   ```bash
   curl -X POST http://localhost:8001/cache/clear
   curl -X POST http://localhost:8001/cache/warm
   ```

4. **Use separate mock server per environment**
   ```bash
   # Dev
   python scripts/mock_prompts_server.py --port 8001

   # Staging
   python scripts/mock_prompts_server.py --port 8002
   ```

5. **Commit cache to git for team sharing** (optional)
   - Add `.prompts_cache/` to git if you want team-wide caching
   - Or keep in `.gitignore` for personal caches
