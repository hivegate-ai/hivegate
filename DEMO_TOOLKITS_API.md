# Demo Toolkits App API Documentation

**Server**: Agent API (demo_toolkits_app.py)
**Version**: 1.1.0
**Default Port**: 8080
**Base URL**: `http://localhost:8080`

This document describes all API endpoints provided by the demo toolkits application, which serves as a standalone chat server for agents with toolkit integration.

---

## Table of Contents

1. [Chat Endpoints](#chat-endpoints)
   - [POST /chat](#post-chat)
   - [POST /chat/commit](#post-chatcommit)
2. [Toolkit Endpoints](#toolkit-endpoints)
   - [GET /toolkit/run](#get-toolkitrun)
   - [POST /toolkit/confirm](#post-toolkitconfirm)
   - [GET /toolkit/executions](#get-toolkitexecutions)
3. [Session Management](#session-management)
   - [POST /session/clear](#post-sessionclear)
4. [Cache Management](#cache-management)
   - [DELETE /cache](#delete-cache)
   - [GET /cache/info](#get-cacheinfo)
5. [Models](#models)

---

## Chat Endpoints

### POST /chat

Start or continue a conversation with an agent.

**Endpoint**: `POST /chat`

**Request Body**:
```json
{
  "message": "Schedule a meeting with Alice tomorrow at 2pm",
  "stream": true,
  "model": "gemini-2.5-pro",
  "user_id": "user123",
  "session_id": "session123",
  "temperature": 0.7,
  "max_tokens": 2048,
  "user_profile": {
    "profile_id": "prof123",
    "email": "user@example.com",
    "full_name": "John Doe",
    "role": "Manager",
    "department": "Engineering",
    "skills": "Python, AI",
    "tools": "Google Calendar, Gmail",
    "org_id": "org123"
  },
  "org_profile": {
    "org_id": "org123",
    "name": "Acme Corp",
    "description": "Technology company",
    "website": "https://acme.com"
  },
  "timezone": "America/New_York",
  "locale": "en-US",
  "images": [
    {
      "content": "base64_encoded_image_data",
      "mime_type": "image/jpeg"
    }
  ],
  "system_prompt": "You are a scheduling expert specialized in team coordination",
  "tools": ["calendar", "email"]
}
```

**Required Fields**:
- `message` (string): User's chat message
- `user_id` (string): User identifier
- `session_id` (string): Session identifier
- `timezone` (string): User timezone (e.g., "America/New_York", "Asia/Jerusalem")
- `locale` (string): User locale (e.g., "en-US", "he-IL")

**Optional Fields**:
- `stream` (boolean): If true, returns SSE stream (default: true)
- `model` (string): Model to use (default: "gemini-2.5-pro")
- `temperature` (float): Temperature for generation
- `max_tokens` (integer): Maximum tokens to generate
- `user_profile` (object): User profile information
- `org_profile` (object): Organization profile information
- `images` (array): Image file data for multimodal messages
- `system_prompt` (string): Custom system prompt/instructions for the agent (default: built-in SYSTEM_PROMPT)
- `tools` (array): List of tool identifiers to include in cache key (e.g., ["calendar", "email"])

**Response (Non-Streaming)**:
```json
{
  "content": "I'll schedule a meeting with Alice tomorrow at 2pm. Let me find an available time slot.",
  "agent_id": "cardsgen",
  "session_id": "session123",
  "model": "gemini-2.5-pro",
  "token_usage": {
    "prompt_tokens": 150,
    "completion_tokens": 50,
    "total_tokens": 200
  },
  "status": "paused",
  "run_id": "run_abc123",
  "tools": [
    {
      "tool_call_id": "call_1",
      "tool_name": "schedule_meeting",
      "requires_confirmation": true,
      "tool_args": {
        "summary": "Meeting with Alice",
        "start": "2025-10-23T14:00:00Z",
        "duration_minutes": 30,
        "attendees": ["alice@example.com"]
      },
      "result": null
    }
  ]
}
```

**Response (Streaming)**:
- **Content-Type**: `text/event-stream`
- **Format**: Server-Sent Events (SSE)

**SSE Event Format**:
```
event: message
data: {"content": "I'll", "status": "running", ...}

event: message
data: {"content": " schedule", "status": "running", ...}

event: done
data: {"status": "complete"}
```

**Example cURL (Default)**:
```bash
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Schedule a meeting with Alice tomorrow at 2pm",
    "user_id": "user123",
    "session_id": "session123",
    "timezone": "America/New_York",
    "locale": "en-US",
    "user_profile": {
      "profile_id": "prof123",
      "email": "user@example.com",
      "full_name": "John Doe",
      "role": "Manager",
      "org_id": "org123"
    }
  }'
```

**Example cURL (Custom System Prompt)**:
```bash
curl -X POST http://localhost:8080/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Find time for team sync",
    "user_id": "user123",
    "session_id": "session123",
    "timezone": "America/New_York",
    "locale": "en-US",
    "system_prompt": "You are a scheduling expert specialized in team coordination",
    "tools": ["calendar", "email"]
  }'
```

**Agent Caching Behavior**:

The server caches agent instances using a CRC32-based cache key computed from:
- `system_prompt` (or default SYSTEM_PROMPT)
- `tools` array (or None)
- `user_id`
- `session_id`

**Cache Key Format**: `"{crc32_hash}:{user_id}:{session_id}"`

**Examples**:
```
Default prompt + no tools:         "4216901897:user123:session123"
Custom prompt + no tools:          "3603457220:user123:session123"
Default prompt + ["calendar"]:     "2253009431:user123:session123"
Custom prompt + ["calendar"]:      "1847562910:user123:session123"
```

**Benefits**:
- ✅ Same configuration reuses cached agent (fast)
- ✅ Different prompts create separate agents (isolated behavior)
- ✅ Tool order doesn't matter (sorted before hashing)
- ✅ Automatic cache differentiation per configuration

---

### POST /chat/commit

Resume a paused agent run with confirmed/edited tools.

**Endpoint**: `POST /chat/commit`

**Description**: Used after `/chat` returns a paused status with tools requiring confirmation. Allows user to confirm, deny, or edit tool parameters before execution.

**Request Body**:
```json
{
  "run_id": "run_abc123",
  "stream": true,
  "model": "gemini-2.5-pro",
  "user_id": "user123",
  "session_id": "session123",
  "updated_tools": [
    {
      "tool_call_id": "call_1",
      "confirmed": true,
      "confirmation_note": "Confirmed by user",
      "tool_args": {
        "summary": "Meeting with Alice (Updated)",
        "start": "2025-10-23T14:00:00Z",
        "duration_minutes": 60,
        "attendees": ["alice@example.com", "bob@example.com"]
      }
    }
  ],
  "user_profile": {
    "profile_id": "prof123",
    "email": "user@example.com",
    "full_name": "John Doe",
    "role": "Manager",
    "org_id": "org123"
  }
}
```

**Required Fields**:
- `run_id` (string): Run ID from paused chat response
- `user_id` (string): User identifier
- `session_id` (string): Session identifier
- `updated_tools` (array): Array of tool objects with confirmations/edits

**Optional Fields**:
- `stream` (boolean): If true, stream via SSE (default: true)
- `model` (string): Model to use (default: "gemini-2.5-pro")
- `user_profile` (object): User profile information

**Response (Non-Streaming)**:
```json
{
  "content": "Meeting scheduled successfully with Alice and Bob for tomorrow at 2pm (60 minutes).",
  "agent_id": "cardsgen",
  "session_id": "session123",
  "model": "gemini-2.5-pro",
  "status": "completed",
  "run_id": "run_abc123",
  "tools": [
    {
      "tool_call_id": "call_1",
      "tool_name": "schedule_meeting",
      "requires_confirmation": true,
      "tool_args": {...},
      "result": {
        "status": "success",
        "event_id": "evt_123",
        "html_link": "https://calendar.google.com/event?eid=..."
      }
    }
  ]
}
```

**Cancellation**: To cancel/deny all tools, set `confirmed: false` for all tools:
```json
{
  "run_id": "run_abc123",
  "user_id": "user123",
  "session_id": "session123",
  "updated_tools": [
    {
      "tool_call_id": "call_1",
      "confirmed": false,
      "confirmation_note": "User cancelled"
    }
  ]
}
```

**Response (Cancelled)**:
```json
{
  "content": "Tool execution cancelled by user.",
  "agent_id": "cardsgen",
  "session_id": "session123",
  "model": "gemini-2.5-pro",
  "status": "cancelled"
}
```

**Example cURL**:
```bash
curl -X POST http://localhost:8080/chat/commit \
  -H "Content-Type: application/json" \
  -d '{
    "run_id": "run_abc123",
    "user_id": "user123",
    "session_id": "session123",
    "updated_tools": [
      {
        "tool_call_id": "call_1",
        "confirmed": true,
        "confirmation_note": "Approved"
      }
    ]
  }'
```

---

## Toolkit Endpoints

### GET /toolkit/run

Direct toolkit method execution endpoint (one-click actions from links).

**Endpoint**: `GET /toolkit/run`

**Description**: Initiates toolkit method execution from query string parameters. Supports both immediate execution and confirmation-required workflows. Typically used for one-click actions from schedule/cancel links.

**Query Parameters**:
- `toolkit_name` (string, required): Name of the toolkit (e.g., "CalendarToolkit")
- `method_name` (string, required): Method name to execute (e.g., "cancel_meeting", "schedule_meeting")
- `user_id` (string, required): User identifier
- `session_id` (string, required): Session identifier
- `organizer_email` (string, required): Organizer email address
- `skip_confirmation` (boolean, optional): Skip confirmation requirement (default: false)
- Additional method-specific parameters (e.g., `event_id`, `summary`, `start`, `duration_minutes`)

**Example URL (Cancel Meeting)**:
```
http://localhost:8080/toolkit/run?toolkit_name=CalendarToolkit&method_name=cancel_meeting&event_id=evt_123&user_id=user123&session_id=session123&organizer_email=user@example.com&skip_confirmation=true
```

**Example URL (Schedule Meeting)**:
```
http://localhost:8080/toolkit/run?toolkit_name=CalendarToolkit&method_name=schedule_meeting&summary=Team%20Meeting&start=2025-10-23T14:00:00Z&duration_minutes=60&attendees=alice@example.com&attendees=bob@example.com&user_id=user123&session_id=session123&organizer_email=user@example.com
```

**Response (Confirmation Required)**:
```json
{
  "status": "confirmation_required",
  "execution_id": "exec_uuid_123",
  "toolkit_name": "CalendarToolkit",
  "method_name": "cancel_meeting",
  "params": {
    "event_id": "evt_123"
  },
  "message": "Confirmation required for cancel_meeting. POST to /toolkit/confirm with execution_id to proceed."
}
```

**Response (Immediate Execution)**:
```json
{
  "status": "success",
  "result": {
    "status": "success",
    "event_id": "evt_123",
    "message": "Event cancelled successfully"
  }
}
```

**Example cURL**:
```bash
curl "http://localhost:8080/toolkit/run?toolkit_name=CalendarToolkit&method_name=cancel_meeting&event_id=evt_123&user_id=user123&session_id=session123&organizer_email=user@example.com&skip_confirmation=true"
```

---

### POST /toolkit/confirm

Confirm and execute a pending toolkit method that requires confirmation.

**Endpoint**: `POST /toolkit/confirm`

**Description**: Confirms a toolkit method execution that was previously cached by `/toolkit/run`. Allows parameter updates before final execution.

**Request Body**:
```json
{
  "execution_id": "exec_uuid_123",
  "toolkit_name": "CalendarToolkit",
  "method_name": "cancel_meeting",
  "confirmed": true,
  "confirmation_note": "User confirmed cancellation",
  "args": {
    "event_id": "evt_123",
    "send_updates": "all"
  }
}
```

**Required Fields**:
- `execution_id` (string): Execution ID from `/toolkit/run` response

**Optional Fields**:
- `toolkit_name` (string): Toolkit name
- `method_name` (string): Method name
- `confirmed` (boolean): Confirmation status
- `confirmation_note` (string): Note about confirmation
- `args` (object): Updated parameters for the method

**Response (Success)**:
```json
{
  "status": "success",
  "message": "[toolkit/confirm] Method cancel_meeting executed successfully",
  "result": {
    "status": "success",
    "event_id": "evt_123",
    "summary": "Team Meeting",
    "message": "Event (ID: evt_123) canceled successfully"
  }
}
```

**Error Responses**:

**404 - Execution Not Found**:
```json
{
  "detail": "Execution exec_uuid_123 not found in cache. It may have expired or been already executed."
}
```

**410 - Execution Expired**:
```json
{
  "detail": "Execution exec_uuid_123 has expired. Please initiate a new execution."
}
```

**TTL**: Cached executions expire after 1 hour (3600 seconds).

**Example cURL**:
```bash
curl -X POST http://localhost:8080/toolkit/confirm \
  -H "Content-Type: application/json" \
  -d '{
    "execution_id": "exec_uuid_123",
    "toolkit_name": "CalendarToolkit",
    "method_name": "cancel_meeting",
    "confirmed": true,
    "args": {
      "event_id": "evt_123"
    }
  }'
```

---

### GET /toolkit/executions

List all pending toolkit executions awaiting confirmation.

**Endpoint**: `GET /toolkit/executions`

**Description**: Returns a list of all toolkit method executions that are currently cached and waiting for confirmation.

**Response**:
```json
{
  "status": "success",
  "executions": [
    {
      "execution_id": "exec_uuid_123",
      "toolkit_name": "CalendarToolkit",
      "method_name": "cancel_meeting",
      "params": {
        "event_id": "evt_123"
      },
      "timestamp": 1698765432.123,
      "age_seconds": 45.678
    },
    {
      "execution_id": "exec_uuid_456",
      "toolkit_name": "CalendarToolkit",
      "method_name": "schedule_meeting",
      "params": {
        "summary": "Team Sync",
        "start": "2025-10-23T14:00:00Z",
        "duration_minutes": 30,
        "attendees": ["alice@example.com"]
      },
      "timestamp": 1698765400.000,
      "age_seconds": 77.801
    }
  ],
  "count": 2
}
```

**Example cURL**:
```bash
curl http://localhost:8080/toolkit/executions
```

---

## Session Management

### POST /session/clear

Clear agent session history from database.

**Endpoint**: `POST /session/clear`

**Description**: Clears conversation history for a specific user/session from the SQLite database. Resets the agent's memory for that session.

**Request Body**:
```json
{
  "message": "",
  "user_id": "user123",
  "session_id": "session123"
}
```

**Required Fields**:
- None (all fields are optional)

**Optional Fields**:
- `message` (string): Can be empty (default: "")
- `user_id` (string): User ID to clear (if not provided, uses default)
- `session_id` (string): Session ID to clear (if not provided, uses default)

**Response (Success)**:
```json
{
  "status": "success",
  "message": "Session cleared for user123:session123"
}
```

**Response (No Active Session)**:
```json
{
  "status": "warning",
  "message": "No active session found for user123:session123"
}
```

**Example cURL**:
```bash
curl -X POST http://localhost:8080/session/clear \
  -H "Content-Type: application/json" \
  -d '{
    "message": "",
    "user_id": "user123",
    "session_id": "session123"
  }'
```

---

## Cache Management

### DELETE /cache

Clear agent cache for specific or all agents.

**Endpoint**: `DELETE /cache`

**Description**: Clears the in-memory agent cache and toolkit execution cache. Can clear for specific user/session or all cached agents.

**Query Parameters**:
- `user_id` (string, optional): User ID filter
- `session_id` (string, optional): Session ID filter

**Response (Specific User/Session)**:
```json
{
  "status": "success",
  "message": "Cleared cache for user123:session123",
  "cleared_executions": 3
}
```

**Response (All Caches)**:
```json
{
  "status": "success",
  "message": "Cleared all cached agents and toolkit executions",
  "cleared_executions": 5
}
```

**Example cURL (Specific)**:
```bash
curl -X DELETE "http://localhost:8080/cache?user_id=user123&session_id=session123"
```

**Example cURL (All)**:
```bash
curl -X DELETE http://localhost:8080/cache
```

---

### GET /cache/info

View cached agents information.

**Endpoint**: `GET /cache/info`

**Description**: Returns information about currently cached agents, including cache keys and counts.

**Response**:
```json
{
  "cached_agents": 3,
  "cache_keys": [
    "user123:session123",
    "user456:session456",
    "user789:session789"
  ]
}
```

**Example cURL**:
```bash
curl http://localhost:8080/cache/info
```

---

## Models

### UserProfile

User profile information.

```typescript
{
  profile_id: string;          // User profile identifier
  email: string;               // User email address
  full_name: string;           // User's full name
  role: string;                // Position/title
  department?: string;         // Department name
  skills?: string;             // User skills
  tools?: string;              // Tools user has access to
  org_id: string;              // Organization identifier
}
```

### OrgProfile

Organization profile information.

```typescript
{
  org_id: string;              // Organization identifier
  name: string;                // Organization name
  description?: string;        // Organization description
  website: string;             // Organization website
}
```

### ChatRequest

Request model for `/chat` endpoint.

```typescript
{
  message: string;                         // User's chat message (required)
  stream: boolean;                         // Enable SSE streaming (default: true)
  model: "gemini-2.5-pro";                 // Model to use (default)
  user_id: string;                         // User identifier (required)
  session_id: string;                      // Session identifier (required)
  temperature?: number;                    // Generation temperature
  max_tokens?: number;                     // Max tokens to generate
  user_profile?: UserProfile;              // User profile
  org_profile?: OrgProfile;                // Organization profile
  timezone: string;                        // User timezone (required)
  locale: string;                          // User locale (required)
  images?: Array<{                         // Multimodal images
    content: string;                       // Base64 encoded content
    mime_type: string;                     // MIME type (e.g., "image/jpeg")
  }>;
  system_prompt?: string;                  // Custom system prompt (default: SYSTEM_PROMPT)
  tools?: Array<string>;                   // Tool identifiers for cache key (e.g., ["calendar", "email"])
}
```

**Agent Caching**:
- The `system_prompt` and `tools` parameters affect agent caching
- Different values create separate cached agents with unique cache keys
- Cache key format: `"{crc32_hash}:{user_id}:{session_id}"`

### CommitRequest

Request model for `/chat/commit` endpoint.

```typescript
{
  run_id: string;                          // Paused run ID (required)
  stream: boolean;                         // Enable SSE streaming (default: true)
  model: "gemini-2.5-pro";                 // Model to use (default)
  user_id: string;                         // User identifier (required)
  session_id: string;                      // Session identifier (required)
  updated_tools: Array<{                   // Tool confirmations/edits (required)
    tool_call_id?: string;                 // Tool call identifier
    confirmed?: boolean;                   // Confirmation status
    confirmation_note?: string;            // Confirmation note
    tool_args?: object;                    // Updated tool arguments
  }>;
  user_profile?: UserProfile;              // User profile
}
```

### ChatResponse

Response model for chat endpoints.

```typescript
{
  content?: string;                        // Agent response content
  agent_id: string;                        // Agent identifier
  session_id?: string;                     // Session identifier
  model: "gemini-2.5-pro";                 // Model used
  token_usage?: {                          // Token usage metrics
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
  status?: string;                         // Status (e.g., "paused", "completed", "cancelled")
  run_id?: string;                         // Run identifier (for paused runs)
  tools?: Array<{                          // Tools requiring confirmation or executed
    tool_call_id?: string;
    tool_name?: string;
    requires_confirmation?: boolean;
    tool_args?: object;
    result?: any;
  }>;
}
```

### ToolkitConfirmRequest

Request model for `/toolkit/confirm` endpoint.

```typescript
{
  execution_id: string;                    // Execution ID (required)
  toolkit_name: string;                    // Toolkit name
  method_name: string;                     // Method name
  confirmed?: boolean;                     // Confirmation status
  confirmation_note?: string;              // Confirmation note
  args?: object;                           // Updated method arguments
}
```

### ToolkitExecutionResponse

Response model for toolkit execution endpoints.

```typescript
{
  status: string;                          // Status (e.g., "success", "confirmation_required")
  message: string;                         // Status message
  result?: object;                         // Execution result
}
```

### ClearSessionRequest

Request model for `/session/clear` endpoint.

```typescript
{
  message: string;                         // Can be empty (default: "")
  user_id?: string;                        // User ID to clear
  session_id?: string;                     // Session ID to clear
}
```

### ClearSessionResponse

Response model for session clearing.

```typescript
{
  status: string;                          // Status (e.g., "success", "warning")
  message: string;                         // Status message
}
```

---

## Error Responses

All endpoints may return the following HTTP error responses:

### 400 Bad Request
```json
{
  "detail": "Invalid request parameters"
}
```

### 404 Not Found
```json
{
  "detail": "Resource not found"
}
```

### 410 Gone
```json
{
  "detail": "Resource has expired"
}
```

### 422 Unprocessable Entity
```json
{
  "detail": "Validation error: [description]"
}
```

### 500 Internal Server Error
```json
{
  "detail": "Internal server error: [description]"
}
```

---

## Environment Variables

The following environment variables configure the demo toolkits app:

```bash
# Service URLs
PROMPTS_SERVICE_URL=https://dev-prompts-service-<project>.run.app
AGENTS_SERVICE_URL=https://dev-hivegate-<project>.run.app

# Google Cloud
GOOGLE_SERVICE_ACCOUNT_KEY_PATH=/path/to/service-account.json

# Model Configuration
GENAI_MODEL_ID=gemini-2.5-flash
GENAI_API_KEY=<your-gemini-api-key>

# Default IDs (for development)
USER_ID=user123
SESSION_ID=session123
ORGANIZER_EMAIL_ADDRESS=user@example.com

# Agent Storage
AGENT_SQLITE_DB=tmp/example.db

# Server Configuration
PORT=8080

# BrightData Configuration (for web scraping toolkits)
BRIGHT_DATA_API_KEY=<your-api-key>
BRIGHT_DATA_WEB_UNLOCKER_ZONE=web_unlocker1
BRIGHT_DATA_SERP_ZONE=serp_api1
```

---

## Running the Server

### Start Server
```bash
python demo_toolkits_app.py
```

### With Custom Environment
```bash
export GENAI_API_KEY="your-api-key"
export GOOGLE_SERVICE_ACCOUNT_KEY_PATH="/path/to/service-account.json"
export PORT=8080
python demo_toolkits_app.py
```

### Server Info
- **Host**: `0.0.0.0`
- **Default Port**: `8080`
- **Auto-reload**: Enabled (for development)
- **Title**: HiveGate Demo API
- **Version**: 1.0.0

---

## Integration Notes

### Relationship to Main API
This is a **standalone chat server** separate from the main hivegate application:

| Feature | demo_toolkits_app.py | hivegate (main) |
|---------|---------------------|------------------|
| **Port** | 8080 | 8000 |
| **Database** | SQLite (agent sessions) | PostgreSQL (agent metadata) |
| **Purpose** | Chat with toolkit integration | Agent CRUD & management |
| **Endpoints** | /chat, /toolkit/* | /v2/agents, /v2/teams, /v2/knowledge |
| **Authentication** | Service account → hivegate | Direct (no auth in dev) |

### Token Management
The demo app fetches OAuth tokens from the main hivegate:
- Uses service account authentication
- Calls `/v2/users/{user_id}/tokens/{integration_key}`
- Supports auto-refresh for expired OAuth2 tokens

### Agent Persistence
- Agents are cached in-memory by `(user_id, session_id)`
- Conversation history stored in SQLite database
- Configurable retention (default: 10 runs)

---

## Best Practices

### 1. Session Management
- Use consistent `user_id` and `session_id` across requests
- Clear sessions when starting new conversations (`/session/clear`)
- Monitor cache size (`/cache/info`)

### 2. Streaming vs Non-Streaming
- Use streaming (`stream: true`) for real-time responses
- Use non-streaming for batch processing or testing
- Default is streaming for better UX

### 3. Tool Confirmation Workflow
```
1. POST /chat → Returns paused status with tools
2. User reviews tools in UI
3. POST /chat/commit → Executes confirmed tools
4. Agent continues with results
```

### 4. Error Handling
- Check `status` field in responses
- Handle `410 Gone` for expired runs/executions
- Implement retry logic for `500` errors
- Cache cleanup on repeated failures

### 5. Performance
- Clear cache periodically (`DELETE /cache`)
- Use appropriate timeouts for long-running operations
- Monitor token usage in responses
- Limit concurrent requests per session

---

## Changelog

### Version 1.1.0 (Current)
- **New**: `system_prompt` parameter in ChatRequest for custom agent instructions
- **New**: `tools` parameter in ChatRequest for cache key differentiation
- **Enhanced**: CRC32-based agent caching with prompt and tools awareness
- **Improved**: Better cache differentiation per agent configuration
- **Backward Compatible**: All new parameters are optional

### Version 1.0.0
- Initial API documentation
- 8 endpoints documented
- Model alignment with V2 API
- Support for UserProfile and OrgProfile
- Multimodal image support
- Toolkit confirmation workflow

---

## Support

For issues or questions:
- Documentation: See `README.md` in project root
- API Docs: See `docs/` directory

---

**Last Updated**: 2025-11-22
**Document Version**: 1.1.0