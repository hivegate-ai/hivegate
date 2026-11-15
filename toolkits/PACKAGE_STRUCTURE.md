# Toolkits Package Structure

## Overview

The `toolkits/` package provides Agno 2.0 agent toolkits for external service integrations.

## Package Structure

```
toolkits/
├── __init__.py              # Package initialization with exports
├── README.md                # Comprehensive package documentation
├── PACKAGE_STRUCTURE.md     # This file
├── API_REFERENCE.md         # Method specifications for all toolkits
├── QUICK_START.md           # Getting started guide
├── token_cache.py           # Shared token caching system
├── calendar.py              # CalendarToolkit (Google/Microsoft Calendar)
├── email.py                 # EmailToolkit (Gmail/Outlook Mail)
├── contacts.py              # ContactsToolkit (Google/Microsoft Contacts)
├── drive.py                 # DriveToolkit (Google Drive/OneDrive)
└── tasks.py                 # TasksToolkit (Google Tasks/Microsoft To Do)
```

## Module Descriptions

### `token_cache.py`
Shared token caching system:
- `TokenCache` class for thread-safe token storage
- TTL-based token expiration
- Multi-service and multi-user support
- Error handling with cooldown periods

### `calendar.py`
Multi-provider calendar management:
- Google Calendar and Microsoft Calendar support
- Event scheduling with conference links
- Free/busy queries for finding available times
- Confirmation-based workflow

### `email.py`
Multi-provider email management:
- Gmail and Microsoft Outlook Mail support
- Send, draft, search, and manage emails
- Gmail link integration for web access
- Attachment metadata support

### `contacts.py`
Multi-provider contact management:
- Google Contacts and Microsoft Contacts support
- Create, update, delete, list, and search contacts
- My Contacts and Other Contacts support

### `drive.py`
Multi-provider file management:
- Google Drive and Microsoft OneDrive support
- Upload, download, list, and manage files
- Knowledge base integration for file indexing

### `tasks.py`
Task management for Google Tasks and Microsoft To Do:
- List task lists and tasks
- Create, update, complete, and delete tasks with confirmation metadata

## Import Examples

```python
# Import all toolkits
from toolkits import CalendarToolkit, EmailToolkit, ContactsToolkit, DriveToolkit, TasksToolkit

# Import specific toolkit
from toolkits.calendar import CalendarToolkit

# Import token cache
from toolkits.token_cache import TokenCache, token_cache
```

## Package Features

### 1. Modular Architecture
Each toolkit is in its own module for easy navigation and maintenance.

### 2. Shared Infrastructure
Common functionality (TokenCache) is centralized to reduce duplication.

### 3. Multi-Provider Support
Each toolkit supports multiple providers (Google, Microsoft) via adapter pattern.

### 4. Confirmation Workflow
Write operations require user confirmation for safety.

### 5. Integration with workspace_suite
Toolkits use `workspace_suite` library for actual API operations.

## Documentation

- `README.md` - Comprehensive package documentation
- `API_REFERENCE.md` - Input/output specifications for all methods
- `QUICK_START.md` - Getting started guide

---

**Maintained By**: Development Team
