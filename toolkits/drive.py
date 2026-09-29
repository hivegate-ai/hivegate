"""
Drive Toolkit

This module provides the DriveToolkit class for multi-provider
file management with support for Google Drive and Microsoft OneDrive.

Usage:
    >>> from toolkits.drive import DriveToolkit
    >>> toolkit = DriveToolkit(
    ...     user_id="user123",
    ...     service_name="google_drive",
    ...     auth=True
    ... )
    >>> # Use with Agno agent
    >>> from agno.agent import Agent
    >>> agent = Agent(tools=[toolkit])
"""

import logging
import mimetypes
from typing import Any, Dict, Optional

from agno.tools import Toolkit

from toolkits.base import BaseToolkit
from workspace_suite import DriveService
from workspace_suite.config import ProviderConfig
from workspace_suite.models import DriveFileResult
from workspace_suite.providers.google_drive import GoogleDriveProvider
from workspace_suite.providers.microsoft_drive import MicrosoftDriveProvider

logger = logging.getLogger(__name__)


# ---------------------------
# Exceptions
# ---------------------------
class DriveAuthError(RuntimeError):
    """Exception raised when drive authentication fails."""

    pass


# ---------------------------
# Drive Toolkit
# ---------------------------
class DriveToolkit(BaseToolkit):
    """
    AI Agent toolkit for file management operations with multi-provider support.

    This toolkit provides intelligent file management capabilities for AI agents,
    supporting both Google Drive and Microsoft OneDrive. It handles OAuth
    authentication, token management, file operations, and provides graceful
    fallbacks when authentication is unavailable.

    ARCHITECTURE
    ============
    The toolkit follows a confirmation-based workflow where the AI agent proposes
    file operations, the user reviews and confirms, and then the operation is executed.
    This prevents unwanted file modifications and allows users to edit details
    before committing.

    Token Management:
    -----------------
    - Uses a shared TokenCache (toolkits.token_cache) for efficient token storage across instances
    - Supports multiple drive providers (Google, Microsoft) via service_name parameter
    - Tokens are cached with TTL-based expiration (default: 5 minutes)
    - Automatic token refresh and error handling with cooldown periods

    Authentication Flow:
    -------------------
    1. If user has valid token → toolkit provides file management tools
    2. If no valid token → toolkit provides auth_required tool
    3. User authenticates via OAuth flow when prompted
    4. Subsequent requests automatically use authenticated service

    SUPPORTED SERVICES
    ==================
    - Google Drive (service_name="google_drive")
    - Microsoft OneDrive (service_name="microsoft_drive")

    TOOLS PROVIDED
    ==============
    When authenticated (auth=True):
    - upload_file: Upload file to drive (requires confirmation)
    - update_file: Update file content (requires confirmation)
    - create_folder: Create new folder (requires confirmation)
    - delete_file: Delete file permanently (requires confirmation)
    - list_files: List files with optional search (no confirmation)
    - get_file_info: Get file metadata (no confirmation)
    - error_card: Display error messages with retry options

    When not authenticated (auth=False):
    - auth_required: Prompt user to authenticate drive service

    CONFIRMATION WORKFLOW
    =====================
    Write operations (upload/update/create/delete) require confirmation:
    1. Agent calls operation with proposed details
    2. Run pauses and returns tool call details to client
    3. Client displays interactive form for user to review/edit
    4. User confirms, skips, or cancels
    5. Client sends confirmed tools via /chat/commit endpoint
    6. Agent resumes and executes the actual drive API call
    7. Operation is executed and confirmation is returned

    USAGE EXAMPLES
    ==============
    Basic initialization:
        >>> toolkit = DriveToolkit(
        ...     user_id="user123",
        ...     service_name="google_drive",
        ...     auth=True
        ... )

    Multi-provider setup:
        >>> google_toolkit = DriveToolkit(
        ...     user_id="user123",
        ...     service_name="google_drive"
        ... )
        >>> microsoft_toolkit = DriveToolkit(
        ...     user_id="user123",
        ...     service_name="microsoft_drive"
        ... )

    No-auth fallback:
        >>> no_auth_toolkit = DriveToolkit(
        ...     user_id="user123",
        ...     service_name="drive",
        ...     auth=False
        ... )

    CLASS ATTRIBUTES
    ================
    http_timeout_s : float
        HTTP request timeout in seconds (default: 15.0)

    INSTANCE ATTRIBUTES
    ===================
    user_id : str
        User identifier for token lookup and attribution

    service_name : str
        Drive service identifier ("google_drive", "microsoft_drive", etc.)

    context : Dict[str, Any]
        Runtime context including token_valid status

    METHODS
    =======
    Public Tools:
    - upload_file(): Upload file to drive (requires confirmation)
    - update_file(): Update file content (requires confirmation)
    - create_folder(): Create new folder (requires confirmation)
    - delete_file(): Delete file permanently (requires confirmation)
    - list_files(): List files with search (no confirmation)
    - get_file_info(): Get file metadata (no confirmation)
    - auth_required(): Prompt for drive authentication
    - error_card(): Display error messages

    Internal Helpers:
    - _prepare_auth(): Manage OAuth token retrieval and validation

    ERROR HANDLING
    ==============
    - DriveAuthError: Token fetch failures, expired tokens
    - HTTP errors: API quota limits, permission issues, network failures
    - Validation errors: Invalid file paths, missing files

    All errors are caught and returned as structured error cards with retry options.

    SECURITY
    ========
    - Tokens never exposed in logs or responses
    - OAuth scopes limited to drive.file (no full drive access)
    - User confirmation required before any file modifications
    - Service account authentication for backend token storage

    INTEGRATION
    ===========
    This toolkit is designed to work with:
    - Agno 2.0 Agent framework
    - FastAPI backend with /chat and /chat/commit endpoints
    - Rich-formatted CLI client for interactive confirmations
    - Backend token service for OAuth token management

    SEE ALSO
    ========
    - TokenCache: Shared token caching system (toolkits.token_cache)
    - fetch_access_token(): Module-level token fetcher (must be provided by application)
    """

    http_timeout_s = 15.0

    def __init__(
        self,
        user_id: str,
        service_name: str,
        tenant_id: str,
        auth: bool = True,
        fetch_token_func=None,
    ):
        """
        Initialize the DriveToolkit.

        Args:
            user_id: User identifier for token lookup (tools_user_id)
            service_name: Drive service name ("google_drive", "microsoft_drive", etc.)
            auth: Whether user is authenticated (determines which tools are available)
            fetch_token_func: Optional function to fetch access tokens. If not provided,
                            toolkit will attempt to import from parent module.
            tenant_id: Tenant ID for knowledge base integration (optional)
        """
        # Store drive-specific attributes FIRST
        self.tenant_id = tenant_id

        # Call BaseToolkit.__init__ which will call _initialize_service()
        BaseToolkit.__init__(self, user_id, service_name, auth, fetch_token_func)

        # Build tools list AFTER service is initialized (methods need self.drive_service)
        tools_list: list = []
        confirmation_tools_list = []

        if auth:
            # Write operations commented out temporarily
            # tools_list.append(self.upload_file)
            # tools_list.append(self.update_file)
            # tools_list.append(self.create_folder)
            # tools_list.append(self.delete_file)
            tools_list.append(self.list_files)
            tools_list.append(self.get_file_info)
            tools_list.append(self.read_file)
            confirmation_tools_list = ["read_file"]
        else:
            tools_list.append(self.drive_auth_required)

        # Call Toolkit base class __init__ LAST with complete tools list
        Toolkit.__init__(
            self,
            name="DriveToolkit",
            tools=tools_list,
            requires_confirmation_tools=confirmation_tools_list,
            show_result_tools=[],
            stop_after_tool_call_tools=([] if auth else ["drive_auth_required"]),
        )

    def _initialize_service(self) -> None:
        """Initialize drive service with appropriate provider."""
        config = ProviderConfig()

        if "google" in self.service_name.lower():
            provider: GoogleDriveProvider | MicrosoftDriveProvider = GoogleDriveProvider(config)
        elif "microsoft" in self.service_name.lower() or "onedrive" in self.service_name.lower():
            provider = MicrosoftDriveProvider(config)  # type: ignore[assignment]
        else:
            # Default to Google
            provider = GoogleDriveProvider(config)

        self.drive_service = DriveService(provider)  # type: ignore[arg-type]

    # ---------------------------
    # Tools
    # ---------------------------

    def upload_file(
        self,
        path: str,
        name: Optional[str] = None,
        parent_folder_id: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Upload a file to Drive.

        This tool uploads a file from local path to the cloud drive. It requires
        confirmation before execution.

        Args:
            path: Local file path to upload
            name: Optional custom name for the uploaded file (defaults to filename from path)
            parent_folder_id: Optional parent folder ID (defaults to root)
            mime_type: Optional MIME type (auto-detected if not provided)

        Returns:
            Dictionary with status and file details:
            {
                "status": "success" | "error",
                "id": "file_id_123",
                "name": "document.pdf",
                "web_view_link": "https://drive.google.com/...",
                "error": {...} (if status=error)
            }
        """
        try:
            # Get authentication token
            token = self._prepare_auth()
            if not token:
                return self.error_card("Authentication required. Please connect your drive service.")

            # Auto-detect MIME type if not provided
            if not mime_type:
                mime_type, _ = mimetypes.guess_type(path)

            # Build parents list if provided
            parents = [parent_folder_id] if parent_folder_id else None

            # Call drive service
            result: DriveFileResult = self.drive_service.upload(
                token=token, path=path, name=name, parents=parents, mime_type=mime_type
            )

            # Return structured response
            if result.status == "success":
                return {
                    "status": "success",
                    "id": result.id,
                    "name": result.name,
                    "web_view_link": result.web_view_link,
                }
            else:
                return {
                    "status": "error",
                    "error": result.error or {"message": "Failed to upload file"},
                }

        except DriveAuthError as e:
            logger.error(f"Auth error in upload_file: {e}")
            return self.error_card(f"Authentication failed: {e}")
        except Exception as e:
            logger.error(f"Error in upload_file: {e}")
            return self.error_card(f"Failed to upload file: {e}")

    def update_file(
        self,
        file_id: str,
        path: str,
        mime_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing file's content.

        This tool replaces the content of an existing file. Requires confirmation before execution.

        Args:
            file_id: ID of the file to update
            path: Local file path with new content
            mime_type: Optional MIME type (auto-detected if not provided)

        Returns:
            Dictionary with status and updated file details
        """
        try:
            # Get authentication token
            token = self._prepare_auth()
            if not token:
                return self.error_card("Authentication required. Please connect your drive service.")

            # Auto-detect MIME type if not provided
            if not mime_type:
                mime_type, _ = mimetypes.guess_type(path)

            # Call drive service
            result: DriveFileResult = self.drive_service.update_content(
                token=token, file_id=file_id, path=path, mime_type=mime_type
            )

            # Return structured response
            if result.status == "success":
                return {
                    "status": "success",
                    "id": result.id,
                    "name": result.name,
                    "web_view_link": result.web_view_link,
                }
            else:
                return {
                    "status": "error",
                    "file_id": file_id,
                    "error": result.error or {"message": "Failed to update file"},
                }

        except DriveAuthError as e:
            logger.error(f"Auth error in update_file: {e}")
            return self.error_card(f"Authentication failed: {e}")
        except Exception as e:
            logger.error(f"Error in update_file: {e}")
            return self.error_card(f"Failed to update file: {e}")

    def create_folder(
        self,
        name: str,
        parent_folder_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Create a new folder in Drive.

        This tool creates a new folder. Requires confirmation before execution.

        Args:
            name: Name for the new folder
            parent_folder_id: Optional parent folder ID (defaults to root)

        Returns:
            Dictionary with status and folder details
        """
        try:
            # Get authentication token
            token = self._prepare_auth()
            if not token:
                return self.error_card("Authentication required. Please connect your drive service.")

            # Build parents list if provided
            parents = [parent_folder_id] if parent_folder_id else None

            # Call drive service
            result: DriveFileResult = self.drive_service.create_folder(token=token, name=name, parents=parents)

            # Return structured response
            if result.status == "success":
                return {
                    "status": "success",
                    "id": result.id,
                    "name": result.name,
                    "web_view_link": result.web_view_link,
                    "type": "folder",
                }
            else:
                return {
                    "status": "error",
                    "error": result.error or {"message": "Failed to create folder"},
                }

        except DriveAuthError as e:
            logger.error(f"Auth error in create_folder: {e}")
            return self.error_card(f"Authentication failed: {e}")
        except Exception as e:
            logger.error(f"Error in create_folder: {e}")
            return self.error_card(f"Failed to create folder: {e}")

    def delete_file(
        self,
        file_id: str,
    ) -> Dict[str, Any]:
        """
        Delete a file or folder permanently.

        This tool permanently deletes a file or folder from Drive.
        Requires confirmation before execution.

        Args:
            file_id: ID of the file or folder to delete

        Returns:
            Dictionary with status, deletion confirmation, and file details:
            {
                "status": "success" | "error",
                "id": "file123",
                "name": "document.pdf",
                "mime_type": "application/pdf",
                "web_view_link": "https://drive.google.com/file/d/file123/view",
                "message": "File deleted successfully"
            }
        """
        try:
            # Get authentication token
            token = self._prepare_auth()
            if not token:
                return self.error_card("Authentication required. Please connect your drive service.")

            # First, fetch file details before deleting
            file_details: Dict[str, Any] = {}
            try:
                import httpx

                headers = {"Authorization": f"Bearer {token}"}
                # Get file metadata
                get_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
                params = {"fields": "id,name,mimeType,webViewLink"}

                client = httpx.Client(timeout=15.0)
                get_resp = client.get(get_url, headers=headers, params=params)  # type: ignore[arg-type]

                if get_resp.status_code == 200:
                    file_data = get_resp.json()
                    file_details = {
                        "name": file_data.get("name", ""),
                        "mime_type": file_data.get("mimeType", ""),
                        "web_view_link": file_data.get("webViewLink", ""),
                    }
                else:
                    logger.warning(f"Failed to fetch file details before deletion: {get_resp.status_code}")
            except Exception as e:
                logger.warning(f"Failed to fetch file details before deleting {file_id}: {e}")

            # Now delete the file using Google Drive API
            try:
                import httpx

                headers = {"Authorization": f"Bearer {token}"}
                delete_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"

                client = httpx.Client(timeout=15.0)
                delete_resp = client.delete(delete_url, headers=headers)

                if delete_resp.status_code in (200, 204):
                    return {
                        "status": "success",
                        "id": file_id,
                        "message": "File deleted successfully",
                        **file_details,  # Include file details if available
                    }
                else:
                    return {
                        "status": "error",
                        "id": file_id,
                        "error": {
                            "message": f"Drive API returned {delete_resp.status_code}",
                            "details": delete_resp.text,
                        },
                    }

            except Exception as e:
                logger.error(f"Failed to delete file {file_id}: {e}")
                return {
                    "status": "error",
                    "id": file_id,
                    "error": {"message": str(e)},
                }

        except DriveAuthError as e:
            logger.error(f"Auth error in delete_file: {e}")
            return self.error_card(f"Authentication failed: {e}")
        except Exception as e:
            logger.error(f"Error in delete_file: {e}")
            return self.error_card(f"Failed to delete file: {e}")

    def list_files(
        self,
        query: Optional[str] = None,
        max_results: int = 100,
    ) -> Dict[str, Any]:
        """
        List files and folders.

        This tool lists files and folders in Drive with optional search query.
        No confirmation required.

        Args:
            query: Optional search query (e.g., "name contains 'document'", "trashed=false")
            max_results: Maximum number of files to return (default: 100)

        Returns:
            Dictionary with status and file list:
            {
                "status": "success" | "error",
                "files": [
                    {
                        "id": "file_id_123",
                        "name": "document.pdf",
                        "web_view_link": "https://...",
                    },
                    ...
                ],
                "total_count": 42
            }
        """
        try:
            # Get authentication token
            token = self._prepare_auth()
            if not token:
                return self.error_card("Authentication required. Please connect your drive service.")

            # Default to non-trashed files if no query specified
            if query is None:
                query = "trashed=false"
            elif "trashed" not in query.lower():
                # Append trashed=false if not already in query
                query = f"{query} and trashed=false"

            logger.info(f"Listing files with query: {query}")

            # Call drive service
            results: list[DriveFileResult] = self.drive_service.list(token=token, q=query, page_size=max_results)

            logger.info(f"Drive list_files returned {len(results)} results")

            # Convert results to dict format
            files = []
            for result in results:
                if result.status == "success":
                    file_dict = {
                        "id": result.id,
                        "name": result.name,
                        "web_view_link": result.web_view_link,
                    }
                    files.append(file_dict)
                elif result.status == "error":
                    # Log errors from individual results
                    logger.error(f"Drive list_files error: {result.error}")
                    return {
                        "status": "error",
                        "error": result.error,
                    }

            response: Dict[str, Any] = {
                "status": "success",
                "files": files,
                "total_count": len(files),
                "query": query,
            }

            # Add helpful message if no files found
            if len(files) == 0:
                response["message"] = (
                    "No files found. Common causes:\n\n"
                    "1. INSUFFICIENT SCOPE: If your token has 'drive.file' scope, it only shows\n"
                    "   files created by THIS app. You need 'drive.readonly' or 'drive' scope\n"
                    "   to see all your files.\n\n"
                    "2. Your Drive might be empty\n\n"
                    "3. Files are in shared folders or Team Drives (requires different queries)\n\n"
                    "4. All files are trashed\n\n"
                    "To fix scope issue: Re-authenticate with broader Drive permissions."
                )

            return response

        except DriveAuthError as e:
            logger.error(f"Auth error in list_files: {e}")
            return self.error_card(f"Authentication failed: {e}")
        except Exception as e:
            logger.error(f"Error in list_files: {e}")
            return self.error_card(f"Failed to list files: {e}")

    def get_file_info(
        self,
        file_id: str,
    ) -> Dict[str, Any]:
        """
        Get file metadata.

        This tool retrieves detailed metadata for a specific file. No confirmation required.

        Args:
            file_id: ID of the file (required)

        Returns:
            Dictionary with status and file metadata:
            {
                "status": "success" | "error",
                "id": "file123",
                "name": "document.pdf",
                "mime_type": "application/pdf",
                "web_view_link": "https://drive.google.com/file/d/file123/view",
                "created_time": "2025-10-20T10:00:00Z",
                "modified_time": "2025-10-23T14:30:00Z",
                "size": "245760",
                "owners": ["user@example.com"]
            }
        """
        try:
            # Get authentication token
            token = self._prepare_auth()
            if not token:
                return self.error_card("Authentication required. Please connect your drive service.")

            # Call drive service
            result: DriveFileResult = self.drive_service.get_file_info(token=token, file_id=file_id)

            # Return structured response
            if result.status == "success":
                response: Dict[str, Any] = {
                    "status": "success",
                    "id": result.id,
                    "name": result.name,
                    "web_view_link": result.web_view_link,
                }

                # Add optional fields if present
                if result.mime_type:
                    response["mime_type"] = result.mime_type
                if result.created_time:
                    response["created_time"] = result.created_time
                if result.modified_time:
                    response["modified_time"] = result.modified_time
                if result.size:
                    response["size"] = result.size
                if result.owners:
                    response["owners"] = result.owners

                return response
            else:
                return {
                    "status": "error",
                    "file_id": file_id,
                    "error": result.error or {"message": "Failed to get file info"},
                }

        except DriveAuthError as e:
            logger.error(f"Auth error in get_file_info: {e}")
            return self.error_card(f"Authentication failed: {e}")
        except Exception as e:
            logger.error(f"Error in get_file_info: {e}")
            return self.error_card(f"Failed to get file info: {e}")

    def read_file(
        self,
        file_id: str,
    ) -> Dict[str, Any]:
        """
        Read a file from Drive and index it into the knowledge base.

        This tool downloads a file from Google Drive, extracts its text content,
        and indexes it into the organization's knowledge base. Once indexed, the
        agent can search and query the file content to answer questions.

        Requires confirmation before execution (modifies knowledge base).

        Args:
            file_id: ID of the file to read (required)

        Returns:
            Dictionary with status and indexing confirmation:
            {
                "status": "success" | "error",
                "message": "File indexed in knowledge base successfully",
                "file_name": "Q3 Report.pdf",
                "file_id": "abc123",
                "knowledge_entry_id": "uuid-xxx",
                "content_type": "application/pdf"
            }

        Supported File Types:
            - PDF documents (.pdf)
            - Word documents (.docx)
            - Text files (.txt)
            - CSV files (.csv)
            - JSON files (.json)
            - HTML files (.html)

        Example Usage:
            User: "Read the Q3 report from my Drive"
            Agent: read_file(file_id="abc123")
            Result: File indexed successfully
            Agent: Can now answer questions about Q3 report content
        """
        try:
            # Get authentication token
            token = self._prepare_auth()
            if not token:
                return self.error_card("Authentication required. Please connect your drive service.")

            import tempfile
            from pathlib import Path

            import httpx

            # Step 1: Get file metadata
            headers = {"Authorization": f"Bearer {token}"}
            metadata_url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
            params = {"fields": "id,name,mimeType"}

            client = httpx.Client(timeout=30.0)
            metadata_resp = client.get(metadata_url, headers=headers, params=params)  # type: ignore[arg-type]

            if metadata_resp.status_code != 200:
                return {
                    "status": "error",
                    "file_id": file_id,
                    "error": {"message": f"Failed to get file metadata: {metadata_resp.status_code}"},
                }

            file_metadata = metadata_resp.json()
            file_name = file_metadata.get("name", "unknown")
            mime_type = file_metadata.get("mimeType", "")

            logger.info(f"[ReadFile] Reading file: {file_name} (mime_type: {mime_type})")

            # Step 2: Download file to temp location
            temp_dir = Path(tempfile.gettempdir()) / "drive_read_files"
            temp_dir.mkdir(exist_ok=True)

            # Generate unique filename to avoid conflicts
            temp_file_path = temp_dir / f"{file_id}_{file_name}"

            # Handle Google Workspace files (need export)
            if mime_type.startswith("application/vnd.google-apps"):
                # Export Google Docs as plain text, Sheets as CSV, etc.
                export_mime_type = "text/plain"
                if "spreadsheet" in mime_type:
                    export_mime_type = "text/csv"
                elif "presentation" in mime_type:
                    export_mime_type = "text/plain"

                download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
                download_params = {"mimeType": export_mime_type}
                download_resp = client.get(download_url, headers=headers, params=download_params)  # type: ignore[arg-type]
            else:
                # Regular file download
                download_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
                download_resp = client.get(download_url, headers=headers)

            if download_resp.status_code != 200:
                return {
                    "status": "error",
                    "file_id": file_id,
                    "file_name": file_name,
                    "error": {"message": f"Failed to download file: {download_resp.status_code}"},
                }

            # Write file to temp location
            temp_file_path.write_bytes(download_resp.content)
            logger.info(f"[ReadFile] Downloaded {len(download_resp.content)} bytes to {temp_file_path}")

            # Step 3: Index file into knowledge base
            try:
                from agno.knowledge.reader.reader_factory import ReaderFactory

                from api.services.knowledge_service import get_knowledge_service

                # Get dynamic knowledge base
                dynamic_kb = get_knowledge_service().get_dynamic_kb()

                # Prepare metadata
                metadata = {
                    "tenant_id": self.tenant_id,
                    "collection_id": None,
                    "file_id": file_id,
                    "original_filename": file_name,
                    "file_type": "company",
                    "content_type": mime_type,
                    "source_id": file_id,
                }

                # Get appropriate reader for the file extension
                file_extension = Path(temp_file_path).suffix
                if not file_extension:
                    # Try to infer from mime type
                    extension_map = {
                        "application/pdf": ".pdf",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                        "text/csv": ".csv",
                        "application/json": ".json",
                        "text/plain": ".txt",
                        "text/html": ".html",
                    }
                    file_extension = extension_map.get(mime_type, ".txt")

                logger.info(f"[ReadFile] Getting reader for extension: {file_extension}")
                reader = ReaderFactory.get_reader_for_extension(file_extension)

                # Read and extract text from the file
                logger.info(f"[ReadFile] Extracting text from {file_name} using {type(reader).__name__}")
                documents = reader.read(temp_file_path)

                # Extract text content from the documents
                if not documents:
                    raise ValueError(f"No content extracted from {file_name}")

                # Combine all document content into one text
                text_content = "\n\n".join([doc.content for doc in documents if hasattr(doc, "content")])

                if not text_content:
                    raise ValueError(f"Extracted empty content from {file_name}")

                logger.info(f"[ReadFile] Extracted {len(text_content)} characters from {file_name}")

                # Add content to knowledge base with extracted text
                dynamic_kb.insert(
                    name=file_id,
                    text_content=text_content,
                    metadata=metadata,
                )

                logger.info(f"[ReadFile] Successfully indexed file '{file_name}' into knowledge base")

                return {
                    "status": "success",
                    "message": f"File '{file_name}' indexed in knowledge base successfully",
                    "file_name": file_name,
                    "file_id": file_id,
                    "content_type": mime_type,
                }

            except Exception as e:
                logger.exception(f"[ReadFile] Failed to index file into knowledge base: {e}")
                return {
                    "status": "error",
                    "file_id": file_id,
                    "file_name": file_name,
                    "error": {"message": f"Failed to index file: {e}"},
                }
            finally:
                # Clean up temp file
                try:
                    if temp_file_path.exists():
                        temp_file_path.unlink()
                        logger.info(f"[ReadFile] Cleaned up temp file: {temp_file_path}")
                except Exception as e:
                    logger.warning(f"[ReadFile] Failed to clean up temp file {temp_file_path}: {e}")

        except DriveAuthError as e:
            logger.error(f"Auth error in read_file: {e}")
            return self.error_card(f"Authentication failed: {e}")
        except Exception as e:
            logger.exception(f"Error in read_file: {e}")
            return self.error_card(f"Failed to read file: {e}")

    def drive_auth_required(self) -> Dict[str, Any]:
        """
        Display an authentication required message to the user.

        This method is called when the user attempts to initiate any drive tool and user not
        authenticated their drive service (Google Drive or Microsoft OneDrive).
        It informs the user that email authentication is required before they can
        create, view, or manage files.

        WHEN CALLED
        - Triggered by the agent when no drive tokens are available

        USER EXPERIENCE
        The user will see a message indicating:
        - Drive authentication is required to create or view files
        - Instructions to connect their Google Drive or Microsoft OneDrive account
        - Available actions: "Connect Drive"

        OUTPUT (JSON)
        Returns an drive-auth-required card:
        {
            "card": "drive-auth-required",
            "context": {
                "token_valid": false
            }
        }
        """
        self._prepare_auth(hard=False)
        return {
            "card": "drive-auth-required",
            "context": dict(self.context),
        }
