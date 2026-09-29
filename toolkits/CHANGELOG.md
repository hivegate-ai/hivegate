# Toolkits Package Changelog

All notable changes to the Toolkits package will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- `API_REFERENCE.md` with complete method specifications for all toolkits

### Removed
- `ExpenseReimbursementToolkit` (moved out of toolkits package)

### Changed
- DriveToolkit updated with `tenant_id` parameter (was `org_id`)
- Documentation files consolidated and updated

### Planned
- Add unit tests in `tests/` subdirectory
- Add integration tests with mock services

## [0.2.1] - 2026-01-20

Initial open source release.

### Added
- **CalendarToolkit**: Multi-provider calendar management (Google Calendar + Microsoft Calendar)
  - `schedule_meeting`: Create calendar events with Google Meet support
  - `schedule_meeting_find_time`: Find available time slots across attendees
  - `cancel_meeting`: Delete calendar events
  - `list_events`: View upcoming/past calendar events
- **EmailToolkit**: Multi-provider email management (Gmail + Microsoft Outlook)
  - `send_email`: Send emails immediately
  - `create_draft`: Create draft emails
  - `send_draft`: Send existing drafts
  - `trash_email`: Move emails to trash
  - `delete_email_permanently`: Permanently delete emails
  - `modify_labels`: Add/remove labels (Gmail only)
  - `search_emails`: Search mailbox with Gmail links
  - `list_drafts`: List draft emails
- **ContactsToolkit**: Multi-provider contact management (Google + Microsoft)
  - `create_contact`: Create new contacts
  - `update_contact`: Update contact fields
  - `delete_contact`: Delete contacts
  - `list_contacts`: List all contacts (My Contacts + Other Contacts)
  - `search_contacts`: Search contacts by query
- **DriveToolkit**: Multi-provider file management (Google Drive + Microsoft OneDrive)
  - `read_file`: Download and index file into knowledge base
  - `upload_file`: Upload file to drive
  - `update_file`: Update file content
  - `create_folder`: Create new folder
  - `delete_file`: Delete file permanently
  - `list_files`: List files with optional search
  - `get_file_info`: Get file metadata
- **TokenCache**: Thread-safe OAuth token caching system
  - Cross-toolkit token sharing
  - Multi-user token isolation
  - TTL-based expiration
  - Error cooldown periods
- Integration with `workspace_suite` library for unified API operations
- Confirmation-based workflow for user safety on write operations
- Schedule links for one-click event creation
- Cancel links for one-click event deletion
- Gmail link transformation for lightweight email metadata

### Security
- Tokens stored in memory only (not persisted to disk)
- No tokens logged or exposed in error messages
- Thread-safe concurrent access to token cache

---

## Contribution Guidelines

### Adding to Changelog

When making changes, add entries under `[Unreleased]` in these categories:

- **Added**: New features, files, or functionality
- **Changed**: Changes to existing functionality
- **Deprecated**: Features that will be removed in future versions
- **Removed**: Removed features or files
- **Fixed**: Bug fixes
- **Security**: Security improvements or vulnerability fixes

### Version Numbering

We use [Semantic Versioning](https://semver.org/):

- **MAJOR** version (X.0.0): Incompatible API changes
- **MINOR** version (0.X.0): New functionality in a compatible manner
- **PATCH** version (0.0.X): Compatible bug fixes

---

[Unreleased]: https://github.com/hivegate-ai/hivegate/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/hivegate-ai/hivegate/releases/tag/v0.2.1
