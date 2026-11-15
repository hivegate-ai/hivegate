# Token Management API

The Agent API provides secure token storage and management for various integrations through the V2 API endpoints. All tokens are encrypted at rest using Fernet symmetric encryption with the `SECRET_TOKEN_ENC_KEY` environment variable.

## Base URL
- Local Development: `http://localhost:8000`
- Token endpoints: `/v2/users/{user_id}/tokens`

## Supported Providers
- `google` - Google OAuth2 and API keys
- `slack` - Slack OAuth2 tokens
- `openai` - OpenAI API keys
- `anthropic` - Anthropic API keys
- `github` - GitHub OAuth2 tokens
- `microsoft` - Microsoft OAuth2 tokens
- `zoom` - Zoom OAuth2 tokens
- `dropbox` - Dropbox OAuth2 tokens
- `notion` - Notion OAuth2 tokens

## Token Types

### OAuth2 Tokens (`oauth2`)
For OAuth2 integrations requiring access and refresh tokens.

**Required fields:**
- `access_token` - The OAuth2 access token

**Optional fields:**
- `refresh_token` - OAuth2 refresh token for auto-refresh
- `token_type` - Token type (usually "Bearer")
- `expires_in` - Token expiration in seconds
- `scope` - Permission scope string

### API Key Tokens (`api_key`)
For services using API keys for authentication.

**Required fields:**
- `api_key` - The API key

**Optional fields:**
- `api_secret` - API secret if required
- `organization_id` - Organization identifier
- `project_id` - Project identifier

### JWT Tokens (`jwt`)
For JWT-based authentication.

**Required fields:**
- `token` - The JWT token

**Optional fields:**
- `algorithm` - Signing algorithm
- `public_key` - Public key for verification
- `issuer` - Token issuer
- `audience` - Token audience

## Tasks connections

The client runs the user's OAuth consent flow and stores the grant with `POST /v2/users/{user_id}/tokens`, using the same user ID it sends to chat.

| Integration key | Provider | Read and write scope | Read-only scope |
|-----------------|----------|----------------------|-----------------|
| `google_tasks` | `google` | `https://www.googleapis.com/auth/tasks` | `https://www.googleapis.com/auth/tasks.readonly` |
| `microsoft_todo` | `microsoft` | `Tasks.ReadWrite` | `Tasks.Read` |

Request offline access (`access_type=offline` for Google, `offline_access` for Microsoft) so the grant includes a refresh token. With a read-only grant, listing works and writes return a permission error.

## Endpoints

### Store Token
**POST** `/v2/users/{user_id}/tokens`

Store or update an encrypted token for a user and integration.

**Path Parameters:**
- `user_id` (string) - User identifier

**Request Body:**
```json
{
  "integration_key": "openai",
  "provider": "openai",
  "token_type": "api_key",
  "token_data": {
    "api_key": "sk-...",
    "organization_id": "org-123"
  },
  "scopes": ["read", "write"],
  "expires_at": "2024-12-31T23:59:59Z"
}
```

**Request Fields:**
- `integration_key` (string, required) - Integration identifier (max 100 chars)
- `provider` (string, required) - Provider name from supported list
- `token_type` (string, required) - Token type: `oauth2`, `api_key`, or `jwt`
- `token_data` (object, required) - Token data matching the token type structure
- `scopes` (array[string], optional) - Permission scopes
- `expires_at` (datetime, optional) - Explicit expiration time

**Response (201 Created):**
```json
{
  "integration_key": "openai",
  "provider": "openai",
  "token_type": "api_key",
  "message": "Token stored successfully for openai",
  "created_at": "2024-01-01T12:00:00Z"
}
```

**OAuth2 Example:**
```json
{
  "integration_key": "google",
  "provider": "google",
  "token_type": "oauth2",
  "token_data": {
    "access_token": "ya29...",
    "refresh_token": "1//04...",
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": "https://www.googleapis.com/auth/userinfo.email"
  }
}
```

### Get Token with Auto-Refresh
**GET** `/v2/users/{user_id}/tokens/{integration_key}`

Retrieve a token with automatic refresh for expired OAuth2 tokens.

**Path Parameters:**
- `user_id` (string) - User identifier
- `integration_key` (string) - Integration identifier

**Response (200 OK):**
```json
{
  "integration_key": "google",
  "provider": "google",
  "token_type": "oauth2",
  "token_data": {
    "access_token": "ya29...",
    "refresh_token": "1//04...",
    "token_type": "Bearer",
    "expires_in": 3600
  },
  "scopes": ["https://www.googleapis.com/auth/userinfo.email"],
  "expires_at": "2024-01-01T13:00:00Z",
  "is_expired": false,
  "refreshed": true
}
```

**Response Fields:**
- `token_data` - Decrypted token data
- `refreshed` (boolean) - Whether token was automatically refreshed
- `is_expired` (boolean) - Current expiration status

### List User Tokens
**GET** `/v2/users/{user_id}/tokens`

List all tokens for a user (metadata only, no sensitive data).

**Path Parameters:**
- `user_id` (string) - User identifier

**Response (200 OK):**
```json
[
  {
    "integration_key": "openai",
    "provider": "openai",
    "token_type": "api_key",
    "scopes": null,
    "expires_at": null,
    "created_at": "2024-01-01T12:00:00Z",
    "updated_at": "2024-01-01T12:00:00Z",
    "is_expired": false
  },
  {
    "integration_key": "google",
    "provider": "google",
    "token_type": "oauth2",
    "scopes": ["https://www.googleapis.com/auth/userinfo.email"],
    "expires_at": "2024-01-01T13:00:00Z",
    "created_at": "2024-01-01T12:00:00Z",
    "updated_at": "2024-01-01T12:30:00Z",
    "is_expired": false
  }
]
```

### Manual Token Refresh
**POST** `/v2/users/{user_id}/tokens/{integration_key}/refresh`

Manually refresh an OAuth2 token.

**Path Parameters:**
- `user_id` (string) - User identifier
- `integration_key` (string) - Integration identifier

**Response (200 OK):**
```json
{
  "integration_key": "google",
  "success": true,
  "message": "Token refreshed successfully",
  "refreshed_at": "2024-01-01T12:30:00Z"
}
```

**Error Response (400 Bad Request):**
```json
{
  "detail": "Token type api_key does not support refresh"
}
```

### Delete Token
**DELETE** `/v2/users/{user_id}/tokens/{integration_key}`

Delete a token (soft delete - marks as inactive).

**Path Parameters:**
- `user_id` (string) - User identifier
- `integration_key` (string) - Integration identifier

**Response (200 OK):**
```json
{
  "integration_key": "google",
  "message": "Token for google deleted successfully"
}
```

## Error Responses

**400 Bad Request:**
- Invalid token type, provider, or data structure
- Empty token data
- Token type doesn't support refresh

**404 Not Found:**
- Token not found for integration

**500 Internal Server Error:**
- Token encryption/decryption failure
- Database errors
- Token refresh service failures

## Auto-Refresh Behavior

OAuth2 tokens are automatically refreshed when:
1. Token is expired or expires within 60 seconds
2. Token has a valid `refresh_token`
3. Provider supports refresh (currently: `google`)
4. Accessed via GET endpoint or explicitly refreshed

Refresh failures return the error but don't delete the token, allowing for manual intervention.

## Security Features

- **Encryption at Rest**: All token data encrypted with Fernet symmetric encryption
- **Environment Key**: Uses `SECRET_TOKEN_ENC_KEY` environment variable
- **Automatic Key Generation**: Generates new encryption key if not provided (with warning)
- **Token Validation**: Validates token structure based on type before storage
- **Secure Deletion**: Soft deletes preserve audit trail while removing access
- **No Token Logging**: Sensitive token data never logged in plaintext

## Environment Variables

- `SECRET_TOKEN_ENC_KEY` - Base64 encryption key for token data (required for production)
- `DB_*` - Database connection variables for token storage

## Usage Examples

### Storing OpenAI API Key
```bash
curl -X POST "http://localhost:8000/v2/users/user123/tokens" \
  -H "Content-Type: application/json" \
  -d '{
    "integration_key": "openai",
    "provider": "openai",
    "token_type": "api_key",
    "token_data": {
      "api_key": "sk-...",
      "organization_id": "org-123"
    }
  }'
```

### Storing Google OAuth2 Token
```bash
curl -X POST "http://localhost:8000/v2/users/user123/tokens" \
  -H "Content-Type: application/json" \
  -d '{
    "integration_key": "google",
    "provider": "google",
    "token_type": "oauth2",
    "token_data": {
      "access_token": "ya29...",
      "refresh_token": "1//04...",
      "client_id": "123...",
      "client_secret": "secret..."
    },
    "expires_at": "2024-01-01T13:00:00Z"
  }'
```

### Retrieving Token with Auto-Refresh
```bash
curl "http://localhost:8000/v2/users/user123/tokens/google"
```

The API will automatically refresh expired OAuth2 tokens and return the updated token data.