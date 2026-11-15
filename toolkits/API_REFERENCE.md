# Toolkit Method Specifications

This document provides complete input/output specifications for all toolkit methods.

> **Note**: URLs shown as `{BASE_URL}` should be replaced with your deployed server URL (e.g., `https://your-app.example.com`).

## CalendarToolkit Methods

### 1. schedule_meeting (requires confirmation)
```python
def schedule_meeting(
    start: str,                                    # ISO datetime or parseable string
    summary: str = "Untitled Meeting",
    timezone_str: Optional[str] = None,            # User timezone (defaults to toolkit's default_timezone)
    attendees: Optional[List[str | Dict[str, str]]] = None,  # ["email"] or [{"email": "..."}]
    location: Optional[str] = None,
    duration_minutes: int = 30,
    description: Optional[str] = "Scheduled via Assistant",
    send_updates: str = "all",                     # "all" | "externalOnly" | "none"
    conference: bool = False,                      # True for Google Meet link
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "event_id": "abc123xyz",
  "html_link": "https://calendar.google.com/event?eid=...",
  "conference_link": "https://meet.google.com/abc-defg-hij",
  "summary": "Team Standup",
  "start": "2025-10-23T14:00:00Z",
  "end": "2025-10-23T14:30:00Z",
  "attendees": [{"email": "alice@example.com", "name": "Alice"}, ...],
  "location": "Conference Room A",
  "timezone": "Asia/Jerusalem"
}
```
**Output (error)**:
```json
{
  "card": "error",
  "message": "Calendar authentication failed. Please reconnect and retry.",
  "actions": ["retry", "cancel"],
  "context": {"token_valid": false}
}
```

### 2. schedule_meeting_find_time (requires confirmation)
```python
def schedule_meeting_find_time(
    summary: str = "Meeting",
    attendees: Optional[List[str]] = None,         # ["email1", "email2"]
    duration_minutes: int = 30,
    search_start_date: Optional[str] = None,       # "YYYY-MM-DD" or "today"
    search_days: int = 3,
    timezone_str: Optional[str] = None,            # User timezone (defaults to toolkit's default_timezone)
    working_hours_start: int = 9,                  # 0-23 (24h format)
    working_hours_end: int = 18,
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "suggested_times": [
    {
      "start": "2025-10-22T14:00:00Z",
      "end": "2025-10-22T14:30:00Z",
      "schedule_link": "{BASE_URL}/toolkit/run?toolkit_name=CalendarToolkit&method_name=schedule_meeting&..."
    },
    {"start": "2025-10-22T15:00:00Z", "end": "2025-10-22T15:30:00Z", "schedule_link": "..."},
    ...
  ],
  "search_period": {"start": "2025-10-22T09:00:00Z", "end": "2025-10-25T18:00:00Z"},
  "attendees_checked": ["organizer@example.com", "alice@example.com"],
  "message": "Found 15 available time slots for a 30-minute meeting",
  "timezone": "Asia/Jerusalem"
}
```
**Output (no slots found)**:
```json
{
  "status": "success",
  "suggested_times": [],
  "message": "No available time slots found for a 30-minute meeting in the search period."
}
```

### 3. cancel_meeting (requires confirmation)
```python
def cancel_meeting(
    event_id: str,                                 # Required: event ID from list_events
    send_updates: str = "all",                     # "all" | "externalOnly" | "none"
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "event_id": "abc123",
  "summary": "Team Meeting",
  "message": "Event (ID: abc123) canceled successfully"
}
```

### 4. list_events (no confirmation)
```python
def list_events(
    max_results: int = 50,
    time_min: Optional[str] = None,                # ISO datetime (default: now)
    time_max: Optional[str] = None,                # ISO datetime (default: now + 30 days)
    search_query: Optional[str] = None,            # Search text
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "events": [
    {
      "event_id": "abc123",
      "summary": "Team Standup",
      "start": "2025-10-23T14:00:00Z",
      "end": "2025-10-23T14:30:00Z",
      "location": "Conference Room A",
      "attendees": ["alice@example.com", "bob@example.com"],
      "organizer": "organizer@example.com",
      "html_link": "https://calendar.google.com/event?eid=...",
      "conference_link": "https://meet.google.com/...",
      "cancel_link": "{BASE_URL}/toolkit/run?toolkit_name=CalendarToolkit&method_name=cancel_meeting&event_id=abc123&..."
    },
    ...
  ],
  "total_count": 12,
  "time_range": {"start": "2025-10-23T00:00:00Z", "end": "2025-11-22T23:59:59Z"}
}
```

### 5. calendar_auth_required (when not authenticated)
```python
def calendar_auth_required() -> Dict[str, Any]
```
**Output**:
```json
{
  "card": "calendar-auth-required",
  "context": {"token_valid": false}
}
```

### 6. error_card
```python
def error_card(message: str = "Unknown error") -> Dict[str, Any]
```
**Output**:
```json
{
  "card": "error",
  "message": "Calendar authentication failed. Please reconnect and retry.",
  "context": {"token_valid": false}
}
```

## EmailToolkit Methods

### 1. send_email (requires confirmation)
```python
def send_email(
    to: List[str],                                 # Required: ["email1", "email2"]
    subject: str,                                  # Required
    body_text: Optional[str] = None,               # Plain text body
    body_html: Optional[str] = None,               # HTML body (Gmail only)
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    thread_id: Optional[str] = None,               # For threading/replies
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "msg123xyz",
  "thread_id": "thread456",
  "to": ["alice@example.com"],
  "subject": "Meeting Follow-up",
  "message": "Email sent successfully"
}
```

### 2. create_draft (requires confirmation)
```python
def create_draft(
    to: List[str],
    subject: str,
    body_text: Optional[str] = None,
    body_html: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    thread_id: Optional[str] = None,
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "draft789",
  "thread_id": "thread456",
  "subject": "Draft: Project Update",
  "message": "Draft created successfully"
}
```

### 3. send_draft (requires confirmation)
```python
def send_draft(
    draft_id: str,                                 # Required: draft ID from create_draft/list_drafts
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "msg123",
  "thread_id": "thread456",
  "message": "Draft sent successfully"
}
```

### 4. search_emails (no confirmation) - Gmail with link transformation
```python
def search_emails(
    query: Optional[str] = None,                   # Gmail search query syntax (optional, defaults to "in:inbox")
    max_results: int = 50,
) -> Dict[str, Any]
```
**Usage**:
- With query: `search_emails(query="from:alice@example.com subject:meeting")` - Search with specific criteria
- Without query: `search_emails()` - Lists inbox emails (equivalent to `query="in:inbox"`)

**Output (success - Gmail)**:
```json
{
  "status": "success",
  "emails": [
    {
      "message_id": "18c5a1b2f3d4e5f6",
      "gmail_link": "https://mail.google.com/mail/u/0/#inbox/18c5a1b2f3d4e5f6",
      "subject": "Meeting tomorrow",
      "from_address": "alice@example.com",
      "from_name": "Alice Smith",
      "date": "2025-10-28T10:30:00-07:00",
      "snippet": "Hi, let's meet tomorrow at 2pm",
      "labels": ["INBOX", "IMPORTANT"],
      "is_unread": true,
      "attachments": [
        {"filename": "document.pdf", "mime_type": "application/pdf", "part_id": "1"}
      ],
      "thread_id": "18c5a1b2f3d4e5f6"
    },
    ...
  ],
  "total_count": 15,
  "query": "from:alice@example.com subject:meeting"
}
```

### 5. list_drafts (no confirmation)
```python
def list_drafts(
    max_results: int = 50,
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "drafts": [
    {"id": "draft123", "subject": "Project Update", "to": ["team@example.com"], "snippet": "..."},
    ...
  ],
  "total_count": 3
}
```

### 6. trash_email (requires confirmation)
```python
def trash_email(
    message_id: str,                               # Required: message ID from search_emails
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "msg123",
  "message": "Email moved to trash"
}
```

### 7. delete_email_permanently (requires confirmation)
```python
def delete_email_permanently(
    message_id: str,
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "msg123",
  "message": "Email permanently deleted"
}
```

### 8. modify_labels (requires confirmation) - Gmail only
```python
def modify_labels(
    message_id: str,
    add_labels: Optional[List[str]] = None,        # ["STARRED", "IMPORTANT"]
    remove_labels: Optional[List[str]] = None,     # ["INBOX", "UNREAD"]
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "msg123",
  "labels_added": ["STARRED"],
  "labels_removed": ["UNREAD"],
  "message": "Labels updated successfully"
}
```

## ContactsToolkit Methods

### 1. create_contact (requires confirmation)
```python
def create_contact(
    given_name: str,                               # Required
    family_name: Optional[str] = None,
    emails: Optional[List[str]] = None,
    phones: Optional[List[str]] = None,
    company: Optional[str] = None,
    job_title: Optional[str] = None,
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "resource_name": "people/c12345",
  "given_name": "John",
  "family_name": "Doe",
  "emails": ["john@example.com"],
  "phones": ["+1234567890"],
  "company": "Acme Inc",
  "job_title": "Engineer"
}
```

### 2. update_contact (requires confirmation)
```python
def update_contact(
    resource_name: str,                            # Required: from list_contacts/search_contacts
    given_name: Optional[str] = None,
    family_name: Optional[str] = None,
    emails: Optional[List[str]] = None,
    phones: Optional[List[str]] = None,
    company: Optional[str] = None,
    job_title: Optional[str] = None,
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "resource_name": "people/c12345",
  "updated_fields": ["emails", "job_title"],
  "given_name": "John",
  "family_name": "Doe",
  "emails": ["john.doe@newcompany.com"],
  "phones": ["+1234567890"],
  "company": "Acme Inc",
  "job_title": "Senior Engineer"
}
```

### 3. delete_contact (requires confirmation)
```python
def delete_contact(
    resource_name: str,                            # Required
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "resource_name": "people/c12345",
  "message": "Contact deleted successfully",
  "given_name": "John",
  "family_name": "Doe",
  "emails": ["john@example.com"],
  "phones": ["+1234567890"],
  "company": "Acme Corp",
  "job_title": "Engineer"
}
```

### 4. list_contacts (no confirmation)
```python
def list_contacts(
    max_results: int = 100,                            # Max per source
) -> Dict[str, Any]
```
**Description**: Lists contacts from both "My Contacts" (explicitly saved) and "Other Contacts" (auto-saved from Gmail). Deduplicates by email address and marks source.

**Important**: For Google Contacts, "Other Contacts" requires the OAuth scope `https://www.googleapis.com/auth/contacts.other.readonly`. If this scope is not granted, only "My Contacts" will be returned. Microsoft Contacts does not have an "Other Contacts" concept.

**Output (success)**:
```json
{
  "status": "success",
  "contacts": [
    {
      "resource_name": "people/c12345",
      "given_name": "John",
      "family_name": "Doe",
      "full_name": "John Doe",
      "emails": ["john@example.com"],
      "phones": ["+1234567890"],
      "company": "Acme Inc",
      "job_title": "Engineer",
      "source": "my_contacts"
    },
    {
      "resource_name": "otherContacts/c67890",
      "given_name": "Alice",
      "emails": ["alice@example.com"],
      "source": "other_contacts"
    },
    ...
  ],
  "total_count": 42,
  "my_contacts_count": 25,
  "other_contacts_count": 20,
  "deduplicated_count": 3
}
```

### 5. search_contacts (no confirmation)
```python
def search_contacts(
    query: str,                                    # Search name, email, phone, etc.
    max_results: int = 100,                        # Max per source
) -> Dict[str, Any]
```
**Description**: Searches contacts in both "My Contacts" (using Google's search API) and "Other Contacts" (via list + client-side filtering). Deduplicates by email address.

**Output (success)**:
```json
{
  "status": "success",
  "query": "john",
  "contacts": [
    {
      "resource_name": "people/c12345",
      "given_name": "John",
      "family_name": "Doe",
      "full_name": "John Doe",
      "emails": ["john@example.com"],
      "phones": ["+1234567890"],
      "company": "Acme Inc",
      "job_title": "Engineer",
      "source": "my_contacts"
    },
    {
      "resource_name": "otherContacts/c99999",
      "given_name": "Johnny",
      "emails": ["johnny@example.com"],
      "source": "other_contacts"
    },
    ...
  ],
  "total_count": 10,
  "my_contacts_count": 7,
  "other_contacts_count": 5,
  "deduplicated_count": 2
}
```

## DriveToolkit Methods

### 1. read_file (requires confirmation)
```python
def read_file(
    file_id: str,                                   # Required: Drive file ID
) -> Dict[str, Any]
```
**Purpose**: Downloads a file from Google Drive and indexes it into the knowledge base for agent querying (modifies knowledge base).

**Output (success)**:
```json
{
  "status": "success",
  "message": "File 'Q3 Report.pdf' indexed in knowledge base successfully",
  "file_name": "Q3 Report.pdf",
  "file_id": "abc123xyz",
  "knowledge_entry_id": "uuid-xxx",
  "content_type": "application/pdf"
}
```

**Supported File Types**:
- PDF documents (.pdf)
- Word documents (.docx)
- Text files (.txt)
- CSV files (.csv)
- JSON files (.json)
- HTML files (.html)
- Google Workspace files (Docs, Sheets, Presentations)

**Integration with Knowledge Base**:
- Downloads file from Google Drive
- Extracts text content using existing knowledge processing pipeline
- Indexes content into organization's knowledge base
- Agent can then search and query the file content
- No need to return raw text - content stays in knowledge base

### 2. upload_file (requires confirmation)
```python
def upload_file(
    file_path: str,                                # Local file path
    folder_id: Optional[str] = None,               # Drive folder ID (None = root)
    file_name: Optional[str] = None,               # Override filename
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "file123abc",
  "name": "document.pdf",
  "web_view_link": "https://drive.google.com/file/d/file123abc/view",
  "message": "File uploaded successfully"
}
```

### 3. update_file (requires confirmation)
```python
def update_file(
    file_id: str,                                  # Required: Drive file ID
    file_path: str,                                # Local file path with new content
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "file123abc",
  "name": "document.pdf",
  "web_view_link": "https://drive.google.com/file/d/file123abc/view",
  "message": "File updated successfully"
}
```

### 4. create_folder (requires confirmation)
```python
def create_folder(
    folder_name: str,                              # Required
    parent_folder_id: Optional[str] = None,        # Parent folder ID (None = root)
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "folder456xyz",
  "name": "Project Documents",
  "web_view_link": "https://drive.google.com/drive/folders/folder456xyz",
  "message": "Folder created successfully"
}
```

### 5. delete_file (requires confirmation)
```python
def delete_file(
    file_id: str,                                  # Required: Drive file ID
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "file123abc",
  "message": "File deleted successfully"
}
```

### 6. list_files (no confirmation)
```python
def list_files(
    folder_id: Optional[str] = None,               # Folder to list (None = root)
    search_query: Optional[str] = None,            # Search query
    max_results: int = 100,
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "files": [
    {
      "id": "file123",
      "name": "document.pdf",
      "mime_type": "application/pdf",
      "web_view_link": "https://drive.google.com/file/d/file123/view",
      "modified_time": "2025-10-23T14:30:00Z",
      "size": "245760"
    },
    ...
  ],
  "total_count": 25
}
```

### 7. get_file_info (no confirmation)
```python
def get_file_info(
    file_id: str,                                  # Required: Drive file ID
) -> Dict[str, Any]
```
**Output (success)**:
```json
{
  "status": "success",
  "id": "file123",
  "name": "document.pdf",
  "mime_type": "application/pdf",
  "web_view_link": "https://drive.google.com/file/d/file123/view",
  "created_time": "2025-10-20T10:00:00Z",
  "modified_time": "2025-10-23T14:30:00Z",
  "size": "245760",
  "owners": ["user@example.com"]
}
```

## TasksToolkit Methods

Construct `TasksToolkit` in Python with a `user_id`, `service_name` (`google_tasks` or `microsoft_todo`), and a token fetcher. The confirmation column describes the Agno registration. Python callers control when to invoke write methods. Native chat and CLI wiring is a separate step.

| Tool | Arguments | Result | Confirmation |
|------|-----------|--------|--------------|
| `list_task_lists` | None | `{"lists": [...]}` | No |
| `list_tasks` | `list_id` | `{"tasks": [...]}` | No |
| `create_task` | `list_id`, `content` | Created task | Yes |
| `update_task` | `list_id`, `task_id`, `changes` | Updated task | Yes |
| `complete_task` | `list_id`, `task_id` | Completed task | Yes |
| `delete_task` | `list_id`, `task_id` | `{"deleted": true, "list_id": "...", "task_id": "..."}` | Yes |

Use the list and task IDs returned by the provider. Lists include open and completed tasks. Task results include `id`, `list_id`, `title`, `notes`, `status` (`open` or `completed`), `due` (a calendar date), and `completed_at` (an ISO timestamp when available).

For creation, supply a title and optional notes and due date, for example `{"content":{"title":"Send report","due":"2026-11-20"}}` alongside `list_id`. For updates, send only the fields you want to change. `{"changes":{"title":"Send final report"}}` preserves notes and due date. `{"changes":{"notes":null,"due":null}}` clears both. Supply due dates as `YYYY-MM-DD`.

If the connection is missing or the provider rejects its credential, the toolkit returns a `tasks-auth-required` card. Its `context.service_name` identifies the rejected connection on reconnect, and `context.available_services` lists connection choices. A permission error may mean the connection only has read access. An interrupted write may have reached the provider, so check the task list before retrying.

## Common Error Responses (All Toolkits)

**Authentication Required**:
```json
{
  "card": "calendar-auth-required" | "email-auth-required" | "contacts-auth-required" | "drive-auth-required",
  "service": "calendar" | "email" | "contacts" | "drive",
  "context": {"token_valid": false}
}
```

**Generic Error**:
```json
{
  "card": "error",
  "message": "Failed to <operation>: <error details>",
  "context": {"token_valid": true | false}
}
```

**API Error**:
```json
{
  "status": "error",
  "error": {
    "message": "Insufficient permissions",
    "code": 403,
    "details": "..."
  }
}
```
