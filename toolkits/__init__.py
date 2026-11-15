"""
Toolkits Package

This package contains AI agent toolkits for various integrations and services.
Each toolkit provides specialized tools that agents can use to interact with
external services, APIs, and systems.

Available Toolkits
==================

Calendar Toolkits:
- CalendarToolkit: Multi-provider calendar management (Google, Microsoft)

Contacts Toolkits:
- ContactsToolkit: Multi-provider contact management (Google, Microsoft)

Drive Toolkits:
- DriveToolkit: Multi-provider file management (Google Drive, OneDrive)

Email Toolkits:
- EmailToolkit: Multi-provider email management (Gmail, Outlook Mail)

Tasks Toolkits:
- TasksToolkit: Multi-provider task management (Google Tasks, Microsoft To Do)

Usage
=====

Import toolkits from this package:

    >>> from toolkits import CalendarToolkit, ContactsToolkit
    >>> from toolkits import DriveToolkit, EmailToolkit
    >>>
    >>> # Calendar toolkit
    >>> calendar_toolkit = CalendarToolkit(
    ...     user_id="user123",
    ...     organizer_email="user@example.com",
    ...     service_name="google_calendar"
    ... )
    >>>
    >>> # Contacts toolkit
    >>> contacts_toolkit = ContactsToolkit(
    ...     user_id="user123",
    ...     service_name="google_contacts"
    ... )
    >>>
    >>> # Drive toolkit
    >>> drive_toolkit = DriveToolkit(
    ...     user_id="user123",
    ...     service_name="google_drive"
    ... )
    >>>
    >>> # Email toolkit
    >>> email_toolkit = EmailToolkit(
    ...     user_id="user123",
    ...     service_name="gmail"
    ... )

Each toolkit is designed to work with the Agno 2.0 agent framework and
follows a confirmation-based workflow for user safety.

Architecture
============

All toolkits share common patterns:
1. OAuth token management via shared TokenCache
2. Confirmation workflow for destructive operations
3. Graceful error handling with user-friendly messages
4. Support for multiple service providers where applicable

Token Management
================

Toolkits use a shared token cache system defined in `token_cache.py`.
This enables:
- Cross-toolkit token sharing
- Multi-user token isolation
- TTL-based token expiration
- Automatic token refresh

See Also
========
- token_cache.py: Shared token caching system
- ../test_cards_app3.py: Integration examples
- ../TOOL_CONFIRMATION_RICH_DIALOGS.md: User interaction documentation
"""

from .base import BaseToolkit
from .calendar import CalendarToolkit
from .contacts import ContactsToolkit
from .drive import DriveToolkit
from .email import EmailToolkit
from .tasks import TasksToolkit
from .token_cache import TokenCache

# Create global shared token cache instance for all toolkits
token_cache = TokenCache()

__all__ = [
    "BaseToolkit",
    "CalendarToolkit",
    "ContactsToolkit",
    "DriveToolkit",
    "EmailToolkit",
    "TasksToolkit",
    "TokenCache",
    "token_cache",
]

__version__ = "2.0.0"
