#!/usr/bin/env python3
"""
Console Chat Client

A simple console-based chat client with extensible dialog support.

ENV VARS:
  AGENT_SERVER_URL (default: http://localhost:8080)
  USER_ID          (default: user123)
  SESSION_ID       (default: random uuid at startup)
  CHAT_STREAM      (default: false)

Run:
  python test_cards_client_tau.py
"""

import importlib
import json
import logging
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import requests

# Import UI components from package (hot-reloadable)
from client_ui import DialogBuilder, ResultFormatter
from PIL import Image as PILImage
from PIL import ImageGrab
from prompt_toolkit import prompt
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings

# ----------------- Configuration -----------------
BASE_SERVER_URL = os.getenv("AGENT_SERVER_URL", "http://127.0.0.1:3002").rstrip("/")
DEFAULT_AGENT_ID = os.getenv("AGENT_ID", "fd5905dc-1bca-494c-adfa-ea9960f4ae52")  # maya

# Available agents for selection
AVAILABLE_AGENTS = {
    "maya": "fd5905dc-1bca-494c-adfa-ea9960f4ae52",
    "nicolle_ossip": "34392e99-1d4e-419a-aea4-a476397396bd",
    "ap_genie": "f4d9337f-d2ee-4d66-a849-d7010a2f175e",
}

USER_ID = os.getenv("USER_ID", "3dda8c97-9786-472a-b636-b52b3f3dbf8a")
ORG_ID = os.getenv("USER_ID", "44a99f99-e673-42fd-ba34-31bc1005d641")

SESSION_ID = os.getenv("SESSION_ID", str(uuid.uuid4()))
STREAM_FLAG = os.getenv("CHAT_STREAM", "false").lower() in ("1", "true", "yes")
DEFAULT_MODEL = os.getenv("CHAT_MODEL", "gemini-3-flash-preview")

# History file location
HISTORY_FILE = Path.home() / ".agent_history"

# Config file location
CONFIG_FILE = Path.home() / ".agent_cli"

# Available models for selection
AVAILABLE_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]


# ----------------- Config Management -----------------
def load_config() -> Dict[str, Any]:
    """Load configuration from config file."""
    if not CONFIG_FILE.exists():
        return {}

    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load config from {CONFIG_FILE}: {e}")
        return {}


def save_config(config: Dict[str, Any]) -> None:
    """Save configuration to config file."""
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        logger.debug(f"Config saved to {CONFIG_FILE}")
    except Exception as e:
        logger.error(f"Failed to save config to {CONFIG_FILE}: {e}")


HELP_TEXT = """
Commands:
/help                 Show this help
/quit                 Exit the client
/stream on|off        Toggle streaming mode
/model [name]         Show current model or set new model
/agent [name]         Show current agent or set new agent
/user [id|email]      Show current user_id or set new user_id (accepts UUID or email)
/session new          Start a new session id (keeps user_id)
/session clear        Clear current chat session (conversation history)
/ids                  Show current ids & settings
/clear                Clear the screen
/commit               Commit last pending tool(s) again (if any)
/reload               Reload formatters and dialogs (hot reload)
/history reload       Reload command history from file
/history save         Save current history to file (overwrites)
/history clear        Clear in-memory history (file unchanged)

Tips:
- Use arrow keys (↑/↓) to navigate command history
- History is saved to ~/.agent_history (append-only on exit)
- Edit ~/.agent_history while running, then use /history reload
- Formatters and dialogs auto-reload when files change
- Set model with: /model gemini-2.5-pro (or use CHAT_MODEL env var)
- Set agent with: /agent maya (or use AGENT_ID env var)
- Set user_id with: /user <uuid> or /user user@example.com (or use USER_ID env var)
"""


# ----------------- Logging Configuration -----------------
LOG_FILE = Path(__file__).parent / "agent_cli.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Create logger
logger = logging.getLogger("agent_cli")
logger.setLevel(logging.DEBUG)

# File handler with rotation (10MB per file, keep 3 backups)
file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=10 * 1024 * 1024,  # 10MB
    backupCount=3,
    encoding="utf-8",
)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

# Console handler for errors only
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)
console_formatter = logging.Formatter("%(levelname)s: %(message)s")
console_handler.setFormatter(console_formatter)
logger.addHandler(console_handler)

logger.info("=" * 80)
logger.info("Agent CLI started")
logger.info(f"Log file: {LOG_FILE}")
logger.info(f"Server URL: {BASE_SERVER_URL}")
logger.info(f"User ID: {USER_ID}")
logger.info(f"Session ID: {SESSION_ID}")
logger.info("=" * 80)


# ----------------- Helper Functions -----------------
def resize_image_if_needed(
    image: PILImage.Image, max_dimension: int = 2048, max_size_mb: float = 2.0
) -> tuple[PILImage.Image, bool, str]:
    """
    Resize image if it exceeds max dimensions or estimated size.

    Args:
        image: PIL Image object to check/resize
        max_dimension: Maximum width or height in pixels
        max_size_mb: Maximum estimated file size in MB

    Returns:
        Tuple of (resized_image, was_resized, resize_info)
    """
    width, height = image.size

    # Estimate file size (rough approximation)
    # RGB: 3 bytes/pixel, RGBA: 4 bytes/pixel, compressed ~30% for JPEG
    channels = len(image.getbands())
    estimated_size_mb = (width * height * channels) / (1024 * 1024)
    if image.format == "JPEG":
        estimated_size_mb *= 0.3  # JPEG compression factor

    # Check if resize needed
    needs_resize = width > max_dimension or height > max_dimension or estimated_size_mb > max_size_mb

    if not needs_resize:
        return image, False, ""

    # Calculate new dimensions (maintain aspect ratio)
    if width > height:
        new_width = min(width, max_dimension)
        new_height = int(height * (new_width / width))
    else:
        new_height = min(height, max_dimension)
        new_width = int(width * (new_height / height))

    # Resize image
    resized = image.copy()
    resized.thumbnail((new_width, new_height), PILImage.Resampling.LANCZOS)

    # Calculate new size estimate
    new_channels = len(resized.getbands())
    new_estimated_size_mb = (new_width * new_height * new_channels) / (1024 * 1024)
    if image.format == "JPEG":
        new_estimated_size_mb *= 0.3

    resize_info = f"Resized: {width}x{height} → {new_width}x{new_height}, ~{estimated_size_mb:.1f}MB → ~{new_estimated_size_mb:.1f}MB"
    logger.info(f"Image resized: {resize_info}")

    return resized, True, resize_info


# ----------------- Chat Client -----------------
class ConsoleChatClient:
    """Main console chat client."""

    def __init__(self):
        # Load saved config
        config = load_config()

        # Initialize with environment variables or defaults, then override with saved config
        self.user_id = config.get("user_id", USER_ID)
        self.session_id = config.get("session_id", SESSION_ID)
        self.stream_flag = config.get("stream", STREAM_FLAG)
        self.model = config.get("model", DEFAULT_MODEL)
        self.agent_id = config.get("agent_id", DEFAULT_AGENT_ID)

        self.result_formatter = ResultFormatter()
        self.dialog_builder = DialogBuilder(self.result_formatter)
        self.running = True

        # Paste detection support
        self.pasted_content: Dict[int, str] = {}
        self.paste_counter = 0

        # Pasted media support (images and PDFs from clipboard)
        self.pasted_media: Dict[int, Any] = {}  # Stores PIL Image objects or file paths
        self.media_counter = 0

        self._setup_file_watcher()
        self._setup_prompt_bindings()

        # Store last server-returned tools payload and run_id to support /commit
        self._last_tools_payload: List[Dict[str, Any]] = []
        self._last_run_id: Optional[str] = None

        # Save current config (in case defaults were used)
        self.save_current_config()

    @property
    def chat_endpoint(self) -> str:
        """Get the current chat endpoint based on agent_id."""
        return f"{BASE_SERVER_URL}/v2/agents/{self.agent_id}/chat"

    @property
    def commit_endpoint(self) -> str:
        """Get the current commit endpoint based on agent_id."""
        return f"{BASE_SERVER_URL}/v2/agents/{self.agent_id}/chat/commit"

    @property
    def clear_session_endpoint(self) -> str:
        """Get the current session clear endpoint based on agent_id."""
        return f"{BASE_SERVER_URL}/v2/agents/{self.agent_id}/session/clear"

    def reload_history(self):
        """Info: History is now handled automatically by prompt_toolkit."""
        print(f"[info] History is automatically loaded from {HISTORY_FILE}")
        print("[info] prompt_toolkit handles history persistence automatically")
        print("[info] No manual reload needed - restart the CLI to reload from file")

    def save_history_now(self):
        """Info: History is now handled automatically by prompt_toolkit."""
        print(f"[info] History is automatically saved to {HISTORY_FILE}")
        print("[info] prompt_toolkit persists history after each command")
        print("[info] No manual save needed")

    def clear_history(self):
        """Info: History clearing not available with prompt_toolkit."""
        print("[info] History clearing not available with prompt_toolkit")
        print(f"[info] To clear history, delete the file: {HISTORY_FILE}")
        print("[info] Then restart the CLI")

    def _setup_file_watcher(self):
        """Setup file watcher for hot reload (optional, requires watchdog)."""
        try:
            from watchdog.events import FileSystemEventHandler  # type: ignore[import-not-found]
            from watchdog.observers import Observer  # type: ignore[import-not-found]

            class UIFileHandler(FileSystemEventHandler):
                def __init__(self, client):
                    self.client = client
                    self.last_reload = 0.0
                    super().__init__()

                def on_modified(self, event):
                    # Only reload .py files in client_ui/
                    if event.is_directory or not event.src_path.endswith(".py"):
                        return
                    if "client_ui" not in event.src_path:
                        return

                    # Debounce: prevent multiple reloads within 1 second
                    import time

                    now = time.time()
                    if now - self.last_reload < 1.0:
                        return
                    self.last_reload = now

                    # Reload in the background
                    print(f"\n[info] File changed: {Path(event.src_path).name}")
                    self.client.reload_ui_components()

            # Start observer in background thread
            client_ui_path = Path(__file__).parent / "client_ui"
            if client_ui_path.exists():
                observer = Observer()
                observer.schedule(UIFileHandler(self), str(client_ui_path), recursive=True)
                observer.daemon = True
                observer.start()
                print(f"[info] File watcher enabled for {client_ui_path}")
        except ImportError:
            # Watchdog not installed, skip auto-reload
            print("[info] Watchdog not installed. Use /reload command for manual reload.")
        except Exception as e:
            print(f"[warn] Could not setup file watcher: {e}")

    def _setup_prompt_bindings(self):
        """Setup prompt_toolkit key bindings for paste detection."""
        self.bindings = KeyBindings()

        @self.bindings.add("<bracketed-paste>")
        def handle_paste(event):
            """Handle bracketed paste event - detect media or store as text."""
            # First, try to detect clipboard content (macOS/Linux)
            try:
                from PIL import ImageGrab

                clipboard_content = ImageGrab.grabclipboard()

                # Handle PIL Image (screenshot or image data)
                if clipboard_content is not None and isinstance(clipboard_content, PILImage.Image):
                    # We have an image in clipboard!
                    self.media_counter += 1
                    media_id = self.media_counter
                    # Store the PIL Image object directly
                    self.pasted_media[media_id] = clipboard_content

                    width, height = clipboard_content.size
                    format_name = clipboard_content.format or "PNG"

                    # Estimate size
                    channels = len(clipboard_content.getbands())
                    estimated_mb = (width * height * channels) / (1024 * 1024)
                    if format_name == "JPEG":
                        estimated_mb *= 0.3

                    # Insert image placeholder
                    placeholder = f"[📷 Clipboard Image, ~{estimated_mb:.1f}MB, {width}x{height}, id={media_id}]"
                    event.current_buffer.insert_text(placeholder)
                    logger.info(f"Detected clipboard image: {width}x{height}, format={format_name}")
                    return

                # Handle list of file paths (copied from Finder on macOS)
                if isinstance(clipboard_content, list) and clipboard_content:
                    files_processed = False
                    for file_path_str in clipboard_content:
                        if not isinstance(file_path_str, str):
                            continue
                        file_path = Path(file_path_str)

                        # Check for image files
                        if file_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                            self.media_counter += 1
                            media_id = self.media_counter
                            self.pasted_media[media_id] = str(file_path)
                            files_processed = True

                            if file_path.exists() and file_path.is_file():
                                try:
                                    with PILImage.open(file_path) as img:
                                        width, height = img.size
                                        size_mb = file_path.stat().st_size / (1024 * 1024)
                                        placeholder = (
                                            f"[📷 {file_path.name}, {size_mb:.1f}MB, {width}x{height}, id={media_id}]"
                                        )
                                        event.current_buffer.insert_text(placeholder + " ")
                                        logger.info(f"Detected clipboard image file: {file_path}")
                                        continue
                                except Exception:
                                    pass
                            placeholder = f"[📷 {file_path.name}, id={media_id}]"
                            event.current_buffer.insert_text(placeholder + " ")

                        # Check for PDF files
                        elif file_path.suffix.lower() == ".pdf":
                            self.media_counter += 1
                            media_id = self.media_counter
                            self.pasted_media[media_id] = str(file_path)
                            files_processed = True

                            if file_path.exists() and file_path.is_file():
                                try:
                                    size_mb = file_path.stat().st_size / (1024 * 1024)
                                    placeholder = f"[📄 {file_path.name}, {size_mb:.1f}MB, id={media_id}]"
                                    event.current_buffer.insert_text(placeholder + " ")
                                    logger.info(f"Detected clipboard PDF: {file_path}")
                                    continue
                                except Exception:
                                    pass
                            placeholder = f"[📄 {file_path.name}, id={media_id}]"
                            event.current_buffer.insert_text(placeholder + " ")

                    # If we processed any files, return early
                    if files_processed:
                        return

            except Exception as e:
                # ImageGrab.grabclipboard() might not work on all platforms or might fail
                logger.debug(f"Clipboard detection failed: {e}")
                pass

            # Get pasted data from event
            data = event.data.strip()

            # Strip surrounding quotes (macOS adds quotes to paths with spaces)
            if (data.startswith("'") and data.endswith("'")) or (data.startswith('"') and data.endswith('"')):
                data = data[1:-1]

            # Check if it's a single-line file path (image or PDF)
            if "\n" not in data:
                # Try to detect if it's a media file
                file_path = Path(data)

                # Check for image files
                if data.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")):
                    # Store in media with ID (even if file doesn't exist yet)
                    self.media_counter += 1
                    media_id = self.media_counter
                    self.pasted_media[media_id] = str(file_path)

                    if file_path.exists() and file_path.is_file():
                        try:
                            with PILImage.open(file_path) as img:
                                width, height = img.size
                                size_mb = file_path.stat().st_size / (1024 * 1024)

                                # Insert media placeholder with size info
                                placeholder = f"[📷 {file_path.name}, {size_mb:.1f}MB, {width}x{height}, id={media_id}]"
                                event.current_buffer.insert_text(placeholder)
                                return
                        except Exception:
                            # Fall through to create placeholder without size info
                            pass

                    # File doesn't exist or couldn't be opened - create placeholder anyway
                    placeholder = f"[📷 {file_path.name}, id={media_id}]"
                    event.current_buffer.insert_text(placeholder)
                    return

                # Check for PDF files
                elif data.lower().endswith(".pdf"):
                    # Store in media with ID (even if file doesn't exist yet)
                    self.media_counter += 1
                    media_id = self.media_counter
                    self.pasted_media[media_id] = str(file_path)

                    if file_path.exists() and file_path.is_file():
                        try:
                            size_mb = file_path.stat().st_size / (1024 * 1024)

                            # Insert media placeholder with size info
                            placeholder = f"[📄 {file_path.name}, {size_mb:.1f}MB, id={media_id}]"
                            event.current_buffer.insert_text(placeholder)
                            return
                        except Exception:
                            # Fall through to create placeholder without size info
                            pass

                    # File doesn't exist - create placeholder anyway
                    placeholder = f"[📄 {file_path.name}, id={media_id}]"
                    event.current_buffer.insert_text(placeholder)
                    return

            # Not a media file - treat as regular text paste
            lines = data.split("\n")
            line_count = len(lines)

            # If text is short (<100 chars) and single-line, expand immediately
            if len(data) < 100 and line_count == 1:
                event.current_buffer.insert_text(data)
                return

            # For longer or multi-line text, store with ID and use placeholder
            self.paste_counter += 1
            paste_id = self.paste_counter
            self.pasted_content[paste_id] = data

            # Insert text placeholder
            placeholder = f"[pasted: {line_count} lines, id={paste_id}]"
            event.current_buffer.insert_text(placeholder)

    def print_ids(self):
        """Display current configuration."""
        # Get agent name if known
        agent_name = None
        for name, agent_id in AVAILABLE_AGENTS.items():
            if agent_id == self.agent_id:
                agent_name = name
                break
        agent_display = f"{agent_name} ({self.agent_id})" if agent_name else self.agent_id

        print(f"[info] server:     {self.chat_endpoint}")
        print(f"[info] agent:      {agent_display}")
        print(f"[info] user_id:    {self.user_id}")
        print(f"[info] session_id: {self.session_id}")
        print(f"[info] stream:     {self.stream_flag}")
        print(f"[info] model:      {self.model}")

    def clear_screen(self):
        """Clear the console screen."""
        os.system("clear" if os.name != "nt" else "cls")

    def reload_ui_components(self):
        """Reload formatters and dialogs without restarting the chat session."""
        try:
            # Reload the modules
            import client_ui.dialogs
            import client_ui.formatters

            importlib.reload(client_ui.formatters)
            importlib.reload(client_ui.dialogs)

            # Recreate instances with new code
            self.result_formatter = client_ui.formatters.ResultFormatter()
            self.dialog_builder = client_ui.dialogs.DialogBuilder(self.result_formatter)

            print(f"[{self.format_timestamp()}] [info] ✓ Reloaded formatters and dialogs")
        except Exception as e:
            print(f"[{self.format_timestamp()}] [error] Failed to reload UI components: {e}")

    def format_timestamp(self) -> str:
        """Get current timestamp for messages."""
        return datetime.now().strftime("%H:%M:%S")

    def get_user_input(self) -> str:
        """Get user input with paste detection using prompt_toolkit."""
        try:
            return prompt(
                "You: ",
                history=FileHistory(str(HISTORY_FILE)),
                key_bindings=self.bindings,
                enable_history_search=True,
                multiline=False,
            )
        except (EOFError, KeyboardInterrupt):
            raise  # Re-raise to be handled by caller

    def expand_paste_placeholders(self, text: str) -> str:
        """Expand paste placeholders to actual pasted content."""

        def replace_placeholder(match):
            paste_id = int(match.group(1))
            return self.pasted_content.get(paste_id, match.group(0))

        # Replace all placeholders with actual content
        # Pattern: [pasted: N lines, id=X]
        return re.sub(r"\[pasted: \d+ lines, id=(\d+)\]", replace_placeholder, text)

    def expand_media_placeholders(self, text: str, media: Dict[str, List[Dict[str, Any]]]) -> str:
        """
        Expand pasted media placeholders to actual files.

        Args:
            text: Message text with placeholders
            media: Media dictionary to append processed images/PDFs to

        Returns:
            Text with expanded placeholders
        """

        def replace_image_placeholder(match):
            media_id = int(match.group(1))
            pasted_img = self.pasted_media.get(media_id)

            if pasted_img is None:
                return match.group(0)  # Keep placeholder if image not found

            # Handle PIL Image objects (clipboard images)
            if isinstance(pasted_img, PILImage.Image):
                # Resize if needed
                resized_img, was_resized, resize_info = resize_image_if_needed(pasted_img)

                # Convert RGBA to RGB (JPEG doesn't support alpha channel)
                if resized_img.mode == "RGBA":
                    rgb_img = PILImage.new("RGB", resized_img.size, (255, 255, 255))
                    rgb_img.paste(resized_img, mask=resized_img.split()[3])
                    resized_img = rgb_img

                # Save to temp file as JPEG
                temp_path = Path(tempfile.gettempdir()) / f"pasted_{media_id}_{uuid.uuid4()}.jpeg"
                resized_img.save(temp_path, "JPEG", quality=85, optimize=True)

                # Get metadata
                width, height = resized_img.size
                size_mb = temp_path.stat().st_size / (1024 * 1024)

                # Read file as bytes and encode as base64
                import base64

                with open(temp_path, "rb") as f:
                    file_bytes = f.read()
                    content_base64 = base64.b64encode(file_bytes).decode("utf-8")

                # Add to media list with base64 content
                media["images"].append(
                    {
                        "content": content_base64,
                        "detail": "high",
                        "mime_type": "image/jpeg",
                        "filepath": str(temp_path),
                    }
                )

                logger.info(f"Clipboard image saved to {temp_path}, size={size_mb:.2f}MB")

                # Show resize info to user if resized
                if was_resized:
                    print(f"  {resize_info}")

                # Remove placeholder from message (media sent in images array)
                return ""

            # Handle string file paths (pasted/dragged image files)
            elif isinstance(pasted_img, str):
                file_path = Path(pasted_img)
                if file_path.exists() and file_path.is_file():
                    # Open image and resize if needed
                    with PILImage.open(file_path) as img:
                        resized_img, was_resized, resize_info = resize_image_if_needed(img)

                        # Convert RGBA to RGB (JPEG doesn't support alpha channel)
                        if resized_img.mode == "RGBA":
                            rgb_img = PILImage.new("RGB", resized_img.size, (255, 255, 255))
                            rgb_img.paste(resized_img, mask=resized_img.split()[3])
                            resized_img = rgb_img

                        # Save to temp file
                        temp_path = Path(tempfile.gettempdir()) / f"pasted_{media_id}_{uuid.uuid4()}.jpeg"
                        resized_img.save(temp_path, "JPEG", quality=85, optimize=True)
                        actual_mime_type = "image/jpeg"

                        size_mb = temp_path.stat().st_size / (1024 * 1024)
                        logger.info(f"Image saved to {temp_path}, size={size_mb:.2f}MB")

                        # Show resize info to user if resized
                        if was_resized:
                            print(f"  {resize_info}")

                    # Read bytes and encode as base64
                    import base64

                    with open(temp_path, "rb") as f:
                        file_bytes = f.read()
                        content_base64 = base64.b64encode(file_bytes).decode("utf-8")

                    # Add to media list with base64 content
                    media["images"].append(
                        {
                            "content": content_base64,
                            "detail": "high",
                            "mime_type": actual_mime_type,
                            "filepath": str(temp_path),
                        }
                    )
                    # Remove placeholder from message (media sent in images array)
                    return ""

            return ""  # Remove placeholder even if image not found

        def replace_pdf_placeholder(match):
            media_id = int(match.group(1))
            pdf_path = self.pasted_media.get(media_id)

            if pdf_path is None:
                return ""  # Remove placeholder even if PDF not found

            # Handle PDF file paths (strings)
            if isinstance(pdf_path, str):
                file_path = Path(pdf_path)
                if file_path.exists() and file_path.is_file():
                    # Read file as bytes and encode as base64
                    import base64

                    with open(file_path, "rb") as f:
                        file_bytes = f.read()
                        content_base64 = base64.b64encode(file_bytes).decode("utf-8")

                    # Add to media list with base64 content
                    media["images"].append(
                        {
                            "content": content_base64,
                            "detail": "high",
                            "mime_type": "application/pdf",
                            "filepath": str(file_path),  # For debugging
                        }
                    )

                # Remove placeholder from message (media sent in images array)
                return ""

            return ""  # Remove placeholder

        # Expand image placeholders:
        # - Clipboard images: [📷 pasted image, WxH, id=X]
        # - File images with metadata: [📷 filename.png, sizeMB, WxH, id=X]
        # - File images without metadata: [📷 filename.png, id=X]
        text = re.sub(r"\[📷 pasted image, \d+x\d+, id=(\d+)\]", replace_image_placeholder, text)
        text = re.sub(r"\[📷 [^,]+, [^,]+, \d+x\d+, id=(\d+)\]", replace_image_placeholder, text)
        text = re.sub(r"\[📷 [^,]+, id=(\d+)\]", replace_image_placeholder, text)

        # Expand PDF placeholders:
        # - With metadata: [📄 filename.pdf, sizeMB, id=X]
        # - Without metadata: [📄 filename.pdf, id=X]
        text = re.sub(r"\[📄 [^,]+, [^,]+, id=(\d+)\]", replace_pdf_placeholder, text)
        text = re.sub(r"\[📄 [^,]+, id=(\d+)\]", replace_pdf_placeholder, text)

        return text

    def detect_media(self, text: str) -> Tuple[str, Dict[str, List[Dict[str, Any]]]]:
        """
        Detect images and PDFs in text and clipboard.

        Returns:
            (cleaned_text, {"images": [...], "pdfs": [...]})
        """
        media: Dict[str, List[Dict[str, Any]]] = {"images": [], "pdfs": []}

        # 1. Check clipboard for images
        try:
            clipboard_img = ImageGrab.grabclipboard()
            if isinstance(clipboard_img, PILImage.Image):
                # Store image with ID (will be saved to temp file when expanding)
                self.media_counter += 1
                media_id = self.media_counter
                self.pasted_media[media_id] = clipboard_img

                # Get image metadata
                width, height = clipboard_img.size

                # Add placeholder to text (will be expanded when sending)
                placeholder = f"[📷 pasted image, {width}x{height}, id={media_id}]"
                text = f"{placeholder} {text}"
        except Exception:
            # Clipboard detection can fail, that's okay
            pass

        # 2. Detect image file paths (handles spaces with backslashes or in absolute paths)
        # Matches: /path/to/file.png, ./file.png, ~/file.png, /path/with\ spaces/file.png, /path/with spaces/file.png
        image_pattern = r"(/[^\n]+\.(?:png|jpg|jpeg|gif|webp|bmp)|~/[^\n]+\.(?:png|jpg|jpeg|gif|webp|bmp)|\./[^\n]+\.(?:png|jpg|jpeg|gif|webp|bmp)|[^\s/]+\.(?:png|jpg|jpeg|gif|webp|bmp))"
        for match in re.finditer(image_pattern, text, re.IGNORECASE):
            file_path = Path(match.group(1))
            if file_path.exists() and file_path.is_file():
                try:
                    with PILImage.open(file_path) as img:
                        width, height = img.size
                        size_mb = file_path.stat().st_size / (1024 * 1024)

                        # Store image with ID (will be processed by expand_media_placeholders)
                        self.media_counter += 1
                        media_id = self.media_counter
                        self.pasted_media[media_id] = str(file_path)

                        # Replace path with placeholder with ID (will be removed by expand_media_placeholders)
                        placeholder = f"[📷 {file_path.name}, {size_mb:.1f}MB, {width}x{height}, id={media_id}]"
                        text = text.replace(match.group(0), placeholder)
                except Exception:
                    # If we can't read the image, leave the path as-is
                    pass

        # 3. Detect PDF file paths (handles spaces with backslashes or in absolute paths)
        # Matches: /path/to/file.pdf, ./file.pdf, ~/file.pdf, /path/with\ spaces/file.pdf, /path/with spaces/file.pdf
        pdf_pattern = r"(/[^\n]+\.pdf|~/[^\n]+\.pdf|\./[^\n]+\.pdf|[^\s/]+\.pdf)"
        for match in re.finditer(pdf_pattern, text, re.IGNORECASE):
            file_path = Path(match.group(1))
            if file_path.exists() and file_path.is_file():
                try:
                    # Store PDF path with ID (will be added to media when expanding)
                    self.media_counter += 1
                    media_id = self.media_counter
                    self.pasted_media[media_id] = str(file_path)

                    # Get metadata
                    size_mb = file_path.stat().st_size / (1024 * 1024)

                    # Replace path with placeholder (will be expanded when sending)
                    placeholder = f"[📄 {file_path.name}, {size_mb:.1f}MB, id={media_id}]"
                    text = text.replace(match.group(0), placeholder)
                except Exception:
                    # If we can't read the PDF, leave the path as-is
                    pass

        # Expand pasted media placeholders (saves images to temp files)
        text = self.expand_media_placeholders(text, media)

        return text, media

    # ---------- Tool commit flow (NEW) ----------
    def _should_wait_for_user_input(self, t: Dict[str, Any]) -> bool:
        """
        Check if tool is waiting for user input/confirmation, using the gateway's approval
        metadata: the call requires confirmation and has neither a decision nor a result yet.
        """
        return t.get("requires_confirmation") is True and t.get("confirmed") is None and t.get("result") is None

    def _extract_tools(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract tools list from a response 'output' or stream event 'data'."""
        tools = data.get("tools")
        if isinstance(tools, list):
            return tools
        # Some payloads may use 'tool_executions' or other legacy keys:
        if isinstance(data.get("tool_executions"), list):
            return data["tool_executions"]
        return []

    def _parse_result(self, result: Any) -> Any:
        """
        Parse tool result, converting string representations to proper dicts.

        Args:
            result: The raw result from the tool (could be string, dict, or other)

        Returns:
            Parsed result as a dict if possible, otherwise the original value
        """
        if result is None:
            return None

        # If it's already a dict, return as-is
        if isinstance(result, dict):
            return result

        # If it's a string, try to parse it as JSON
        if isinstance(result, str):
            # Try JSON parsing first
            try:
                return json.loads(result)
            except json.JSONDecodeError:
                pass

            # Try ast.literal_eval for Python dict strings like "{'key': 'value'}"
            try:
                import ast

                return ast.literal_eval(result)
            except (ValueError, SyntaxError):
                pass

        # Return original if we can't parse it
        return result

    def _clear_committed_batch(self):
        """Forget the batch /commit would reopen. A later paused batch is remembered again when it arrives."""
        self._last_tools_payload = []
        self._last_run_id = None

    def _prompt_and_commit_tools_if_needed(self, data: Dict[str, Any]):
        """
        If server returned confirmable tool calls, prompt the user to confirm/edit,
        then call /chat/commit with the updated tools list once.
        """
        # Extract tools first
        tools = self._extract_tools(data)

        # Check if any tools require confirmation
        confirmables = [t for t in tools if self._should_wait_for_user_input(t)]
        if not confirmables:
            return  # nothing to commit

        # Check if response status is paused (or has confirmable tools)
        status = data.get("status", "").lower()
        if status not in ("paused", "unknown", ""):
            # If status is explicitly set to something other than paused, and we have confirmables,
            # warn but don't return (the server may have forgotten to set status to paused)
            logger.warning(f"Found confirmable tools but status is '{status}', proceeding anyway")

        # Extract run_id from the response data
        run_id = data.get("run_id")
        if not run_id:
            # Fallback: check if it's nested in output
            run_id = data.get("output", {}).get("run_id")

        if not run_id:
            print("[warn] No run_id found in response, cannot commit tools")
            return

        # Remember last tools and run_id for /commit command
        self._last_tools_payload = tools
        self._last_run_id = run_id

        print("\n=== Pending tool confirmation(s) ===")
        for idx, t in enumerate(confirmables, 1):
            print(f" [{idx}] {t.get('tool_name') or t.get('name')} id={t.get('tool_call_id') or t.get('id')}")
            print(f"      args: {json.dumps(t.get('tool_args') or t.get('args') or {}, ensure_ascii=False)}")

        # NEW: Use InquirerPy to handle each confirmable tool
        # Start from copies of the entire tools payload, so /commit can reopen an uncommitted batch
        updated_tools: List[Dict[str, Any]] = [dict(t) for t in tools]
        decided_tools = []
        should_cancel = False

        for confirmable in confirmables:
            try:
                result = self.dialog_builder.build_dialog_for_tool(confirmable)
                action = result["action"]
                edited_args = result["edited_args"]

                if action == "cancel":
                    print("[info] Cancelled tool confirmation.")
                    should_cancel = True
                    break
                elif action == "skip":
                    print(f"[info] Skipped tool: {confirmable.get('tool_name') or confirmable.get('name')}")
                    continue
                elif action == "decline":
                    # The run resumes and the model is told the call was declined, with the note
                    tool_id = confirmable.get("tool_call_id") or confirmable.get("id")
                    for t in updated_tools:
                        if (t.get("tool_call_id") or t.get("id")) == tool_id:
                            t["confirmed"] = False
                            t["confirmation_note"] = result.get("note") or "Declined by the user."
                            decided_tools.append(t)
                            break
                    print(f"[info] Declined tool: {confirmable.get('tool_name') or confirmable.get('name')}")
                elif action == "confirm":
                    # Update the tool's args in the payload
                    tool_id = confirmable.get("tool_call_id") or confirmable.get("id")
                    for t in updated_tools:
                        if (t.get("tool_call_id") or t.get("id")) == tool_id:
                            # Update args with edited values
                            if "tool_args" in t:
                                t["tool_args"] = edited_args
                            else:
                                t["args"] = edited_args
                            # Mark as confirmed
                            t["confirmed"] = True
                            t["confirmation_note"] = (t.get("tool_name") or t.get("name") or "confirm").replace(
                                "_", "-"
                            )
                            decided_tools.append(t)
                            break
                    print(f"[info] Confirmed tool: {confirmable.get('tool_name') or confirmable.get('name')}")

            except (KeyboardInterrupt, EOFError):
                print("\n[info] Confirmation interrupted by user.")
                should_cancel = True
                break

        # A batch with at least one confirmed or declined call is committed; one that is all skipped is not
        if should_cancel or not decided_tools:
            if not decided_tools and not should_cancel:
                print("[info] No tools confirmed or declined.")
            return

        # Commit
        commit_payload = {
            "run_id": run_id,
            "updated_tools": updated_tools,
            "stream": self.stream_flag,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "model": self.model,
            "timezone": "Asia/Jerusalem",  # User timezone
            "locale": "en-US",  # User locale (language tag format)
            "user_profile": {
                "profile_id": "id123",
                "full_name": "Test User",
                "email": "user@example.com",
                "role": "user",
                "department": "",
                "skills": "",
                "tools": "",
                "org_id": ORG_ID,
            },
        }
        print("\n[commit] POST /chat/commit ...")
        print(f"[debug] Sending {len(updated_tools)} tools")

        # Handle streaming vs non-streaming commit
        if self.stream_flag:
            # Use SSE streaming for commit response
            self._handle_commit_sse_stream(commit_payload)
        else:
            # Non-streaming commit
            try:
                r = requests.post(self.commit_endpoint, json=commit_payload, timeout=180)
            except requests.RequestException as e:
                print(f"[error] commit failed: {e}")
                return

            if r.status_code != 200:
                print(f"[error] {r.status_code}: {r.text}")
                return

            # The gateway accepted the batch: /commit must not offer it again
            self._clear_committed_batch()
            resp = r.json()
            self.handle_single_response(resp)

    # ---------- Existing handlers ----------
    def handle_single_response(self, resp: Dict[str, Any]):
        """Handle non-streaming response."""
        out = resp.get("output") or resp
        text = out.get("content")
        tools = self._extract_tools(out)

        # Display tool calls if any
        if tools:
            print(f"\n[{self.format_timestamp()}] Tools:")
            for t in tools:
                call_id = t.get("tool_call_id") or t.get("id") or "-"
                call_name = t.get("tool_name") or "Un-named"
                tool_args = t.get("tool_args") or {}
                result = self._parse_result(t.get("result"))
                print(f"  • {call_name} (id={call_id})")
                print(f"    args: {json.dumps(tool_args, ensure_ascii=False)}")

                # Display result (both plain and Rich formatted)
                if result is not None:
                    print(f"    result: {json.dumps(result, ensure_ascii=False)}")
                    self.result_formatter.format_result(t, result)

        # Display assistant response
        if text:
            print(f"\n[{self.format_timestamp()}] Assistant:\n{text}")
        else:
            print(f"\n[{self.format_timestamp()}] Assistant: (no text)")

        # NEW: Prompt/commit if tools require confirmation
        self._prompt_and_commit_tools_if_needed(out)

    def handle_stream_response(self, resp: Dict[str, Any]):
        """Handle streaming response (legacy JSON mode)."""
        events_raw = resp.get("events") or resp
        events: List[Dict[str, Any]] = events_raw if isinstance(events_raw, list) else [events_raw]
        if not events:
            print(f"\n[{self.format_timestamp()}] [stream] no events")
            return

        for i, ev in enumerate(events, start=1):
            data = ev.get("data") or {}
            text = data.get("content")

            if text:
                print(f"\n[{self.format_timestamp()}] [stream {i}]:\n{text}")

            tools = self._extract_tools(data)
            if tools:
                print(f"\n[{self.format_timestamp()}] [stream {i}] Tools:")
                for t in tools:
                    call_id = t.get("tool_call_id") or t.get("id") or "-"
                    call_name = t.get("tool_name") or "Un-named"
                    tool_args = t.get("tool_args") or {}
                    result = self._parse_result(t.get("result"))
                    print(f"  • {call_name} (id={call_id})")
                    print(f"    args: {json.dumps(tool_args, ensure_ascii=False)}")

                    # Display result (both plain and Rich formatted)
                    if result is not None:
                        print(f"    result: {json.dumps(result, ensure_ascii=False)}")
                        self.result_formatter.format_result(t, result)

            # Check if this event requires tool confirmation
            self._prompt_and_commit_tools_if_needed(data)

    def handle_sse_stream(self, message: str, media: Optional[Dict[str, List[Dict[str, Any]]]] = None):
        """Handle Server-Sent Events (SSE) streaming."""
        logger.debug(f"handle_sse_stream received media: {media is not None}")
        if media is None:
            media = {"images": [], "pdfs": []}
        logger.debug(f"After None check, media has {len(media.get('images', []))} images")

        payload = {
            "message": message,
            "stream": True,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "model": self.model,
            "timezone": "Asia/Jerusalem",  # User timezone
            "locale": "en-US",  # User locale (language tag format)
            "user_profile": {
                "profile_id": "id123",
                "full_name": "Test User",
                "email": "user@example.com",
                "role": "user",
                "department": "",
                "skills": "",
                "tools": "",
                "org_id": ORG_ID,
            },
            "org_profile": {
                "org_id": ORG_ID,
                "name": "Assistant",
                "description": "",
                "website": "https://bzgenie.ai",
            },
        }

        # Add images to payload if any were detected
        if media["images"]:
            payload["images"] = media["images"]
            logger.debug(f"Sending {len(media['images'])} images/PDFs:")
            for img in media["images"]:
                logger.debug(f"  - {img.get('filepath')} ({img.get('mime_type')})")

        # Log full payload with truncated base64 content
        payload_for_log = payload.copy()
        if "images" in payload_for_log:
            payload_for_log["images"] = [
                {**img, "content": img["content"][:100] + "...[truncated]"} for img in payload_for_log["images"]
            ]
        logger.info(f"Chat request (streaming): {json.dumps(payload_for_log, indent=2)}")

        # Track state from last event for confirmation check at end
        last_event_tools: List[Dict[str, Any]] = []
        last_event_status = "unknown"
        last_event_run_id = None

        try:
            with httpx.Client(timeout=300.0) as client:
                with client.stream("POST", self.chat_endpoint, json=payload) as response:
                    if response.status_code != 200:
                        response.read()
                        print(f"[error] {response.status_code}: {response.text}")
                        return

                    event_count = 0
                    current_event_type = None
                    content_buffer = ""  # Buffer for accumulating streaming content
                    first_content = True

                    print(f"\n[{self.format_timestamp()}] [SSE stream started]")

                    for line in response.iter_lines():
                        line = line.strip()
                        if not line:
                            continue

                        if line.startswith("event:"):
                            current_event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_str = line[5:].strip()
                            try:
                                data = json.loads(data_str)
                                logger.debug(
                                    f"SSE event #{event_count + 1} ({current_event_type or 'message'}): {json.dumps(data, indent=2)[:200]}..."
                                )
                                new_content = self._process_sse_event(
                                    current_event_type or "message", data, content_buffer, first_content
                                )
                                if new_content:
                                    content_buffer += new_content
                                    first_content = False
                                event_count += 1

                                # Keep state from last event (will be replaced by next event)
                                if "status" in data:
                                    last_event_status = data["status"]
                                if "run_id" in data:
                                    last_event_run_id = data["run_id"]

                                # Keep tools from last event (will be replaced by next event)
                                tools = self._extract_tools(data)
                                if tools:
                                    last_event_tools = tools
                                    logger.debug(
                                        f"Last event had {len(tools)} tools, last tool: {tools[-1].get('tool_name')}"
                                    )

                            except json.JSONDecodeError as e:
                                print(f"[warn] Failed to parse SSE data: {e}")
                                print(f"[warn] Raw data: {data_str[:100]}")
                            current_event_type = None

                    print(f"\n\n[{self.format_timestamp()}] [SSE stream ended - {event_count} events]")

                    # After stream ends, check the last tool from the last event for confirmation
                    if last_event_tools:
                        accumulated_data = {
                            "tools": last_event_tools,  # The paused batch from the last event
                            "status": last_event_status,
                            "run_id": last_event_run_id,
                        }
                        self._prompt_and_commit_tools_if_needed(accumulated_data)

        except httpx.RequestError as e:
            print(f"[error] SSE request failed: {e}")
        except KeyboardInterrupt:
            print(f"\n[{self.format_timestamp()}] [SSE stream interrupted by user]")

    def _handle_commit_sse_stream(self, commit_payload: Dict[str, Any]):
        """Handle commit response as SSE stream."""
        # Track state from last event for confirmation check at end
        last_event_tools: List[Dict[str, Any]] = []
        last_event_status = "unknown"
        last_event_run_id = None

        try:
            with httpx.Client(timeout=300.0) as client:
                with client.stream("POST", self.commit_endpoint, json=commit_payload) as response:
                    if response.status_code != 200:
                        response.read()
                        print(f"[error] {response.status_code}: {response.text}")
                        return

                    # The gateway accepted the batch: /commit must not offer it again
                    self._clear_committed_batch()

                    # The gateway answers in JSON, even for a streaming commit, when every call
                    # is declined for a run that is no longer cached
                    if response.headers.get("content-type", "").startswith("application/json"):
                        response.read()
                        self.handle_single_response(response.json())
                        return

                    event_count = 0
                    current_event_type = None
                    content_buffer = ""
                    first_content = True

                    print(f"\n[{self.format_timestamp()}] [commit SSE stream started]")

                    for line in response.iter_lines():
                        line = line.strip()
                        if not line:
                            continue

                        if line.startswith("event:"):
                            current_event_type = line[6:].strip()
                        elif line.startswith("data:"):
                            data_str = line[5:].strip()
                            try:
                                data = json.loads(data_str)
                                logger.debug(
                                    f"Commit SSE event #{event_count + 1} ({current_event_type or 'message'}): {json.dumps(data, indent=2)[:200]}..."
                                )
                                new_content = self._process_sse_event(
                                    current_event_type or "message", data, content_buffer, first_content
                                )
                                if new_content:
                                    content_buffer += new_content
                                    first_content = False
                                event_count += 1

                                # Keep state from last event (will be replaced by next event)
                                if "status" in data:
                                    last_event_status = data["status"]
                                if "run_id" in data:
                                    last_event_run_id = data["run_id"]

                                # Keep tools from last event (will be replaced by next event)
                                tools = self._extract_tools(data)
                                if tools:
                                    last_event_tools = tools
                                    logger.debug(
                                        f"Last event had {len(tools)} tools, last tool: {tools[-1].get('tool_name')}"
                                    )

                            except json.JSONDecodeError as e:
                                print(f"[warn] Failed to parse commit SSE data: {e}")
                                print(f"[warn] Raw data: {data_str[:100]}")
                            current_event_type = None

                    print(f"\n\n[{self.format_timestamp()}] [commit SSE stream ended - {event_count} events]")

                    # After stream ends, check the last tool from the last event for confirmation (chain commits)
                    if last_event_tools:
                        accumulated_data = {
                            "tools": [last_event_tools[-1]],  # Only the last tool from last event
                            "status": last_event_status,
                            "run_id": last_event_run_id,
                        }
                        self._prompt_and_commit_tools_if_needed(accumulated_data)

        except httpx.RequestError as e:
            print(f"[error] Commit SSE request failed: {e}")
        except KeyboardInterrupt:
            print(f"\n[{self.format_timestamp()}] [commit SSE stream interrupted by user]")

    def _process_sse_event(
        self, event_type: str, data: Dict[str, Any], content_buffer: str, first_content: bool
    ) -> Optional[str]:
        """
        Process a single SSE event.

        Returns:
            New content string to add to buffer, or None if no content
        """
        ts = self.format_timestamp()

        if event_type == "done":
            status = data.get("status", "unknown")
            count = data.get("event_count", 0)
            print(f"\n[{ts}] [✓] Stream complete: {status} ({count} events)")
            return None

        if event_type == "error":
            error_msg = data.get("error", "Unknown error")
            print(f"\n[{ts}] [✗] Error: {error_msg}")
            return None

        # Handle streaming content
        content = data.get("content")
        if content:
            if first_content:
                # First chunk - print header and content
                print(f"\n[{ts}] Assistant:\n{content}", end="", flush=True)
            else:
                # Subsequent chunks - just print the content (no newline/timestamp)
                print(content, end="", flush=True)

        # Handle tools (only shown after streaming is done or on tool events)
        tools = self._extract_tools(data)
        if tools:
            print(f"\n\n[{ts}] Tools:")
            for t in tools:
                call_id = t.get("tool_call_id") or t.get("id") or "-"
                call_name = t.get("tool_name") or "Un-named"

                # Skip internal system tools
                if call_name == "get_chat_history":
                    continue

                tool_args = t.get("tool_args") or {}
                result = self._parse_result(t.get("result"))
                print(f"  • {call_name} (id={call_id})")
                print(f"    args: {json.dumps(tool_args, ensure_ascii=False)}")

                # Display result (both plain and Rich formatted)
                if result is not None:
                    print(f"    result: {json.dumps(result, ensure_ascii=False)}")
                    self.result_formatter.format_result(t, result)

        return content

    def _safe_get(self, d: Dict[str, Any], path: List[str], default=None):
        """Safely get nested dictionary value."""
        cur = d
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur

    def clear_session(self):
        """Clear the current chat session on the server."""
        payload = {
            "message": "",  # Required by ChatRequest but not used
            "user_id": self.user_id,
            "session_id": self.session_id,
        }

        try:
            r = requests.post(self.clear_session_endpoint, json=payload, timeout=30)
        except requests.RequestException as e:
            print(f"[error] Failed to clear session: {e}")
            return

        if r.status_code != 200:
            print(f"[error] {r.status_code}: {r.text}")
            return

        resp = r.json()
        status = resp.get("status", "unknown")
        message = resp.get("message", "")

        if status == "success":
            print(f"[info] ✓ {message}")
        elif status == "warning":
            print(f"[warn] {message}")
        else:
            print(f"[info] {message}")

    def save_current_config(self):
        """Save current configuration to config file."""
        config = {
            "user_id": self.user_id,
            "session_id": self.session_id,
            "stream": self.stream_flag,
            "model": self.model,
            "agent_id": self.agent_id,
        }
        save_config(config)

    def lookup_user_by_email(self, email: str) -> Optional[str]:
        """
        Lookup user ID by email address.

        Args:
            email: User email address

        Returns:
            User ID if found, None otherwise
        """
        # Try to call user lookup endpoint (if it exists)
        endpoint = f"{BASE_SERVER_URL}/v2/users/lookup"
        try:
            r = requests.get(endpoint, params={"email": email}, timeout=30)
            if r.status_code == 200:
                user_data = r.json()
                return user_data.get("user_id") or user_data.get("id")
        except requests.RequestException:
            pass

        # Endpoint doesn't exist or failed
        return None

    def send_message(self, message: str):
        """Send a message to the server."""
        # First expand paste placeholders to get actual file paths
        message = self.expand_paste_placeholders(message)

        # Then detect media (images and PDFs) in expanded message
        processed_message, media = self.detect_media(message)

        logger.debug(f"After detect_media: {len(media.get('images', []))} images found")
        if media.get("images"):
            for img in media["images"]:
                logger.debug(f"  - {img.get('filepath')}")

        # Use SSE streaming when stream flag is enabled
        if self.stream_flag:
            self.handle_sse_stream(processed_message, media)
            return

        # Non-streaming mode: use regular JSON request
        payload = {
            "message": processed_message,
            "stream": False,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "model": self.model,
            "timezone": "Asia/Jerusalem",  # User timezone
            "locale": "en-US",  # User locale (language tag format)
            "user_profile": {
                "profile_id": "id123",
                "full_name": "Test User",
                "email": "user@example.com",
                "role": "user",
                "department": "",
                "skills": "",
                "tools": "",
                "org_id": ORG_ID,
            },
            "org_profile": {
                "org_id": ORG_ID,
                "name": "Assistant",
                "description": "",
                "website": "https://bzgenie.ai",
            },
        }

        # Add images to payload if any were detected
        if media["images"]:
            payload["images"] = media["images"]
            logger.debug(f"Non-streaming: Sending {len(media['images'])} images/PDFs:")
            for img in media["images"]:
                logger.debug(f"  - {img.get('filepath')} ({img.get('mime_type')})")

        # Log full payload with truncated base64 content
        payload_for_log = payload.copy()
        if "images" in payload_for_log:
            payload_for_log["images"] = [
                {**img, "content": img["content"][:100] + "...[truncated]"} for img in payload_for_log["images"]
            ]
        logger.info(f"Chat request (non-streaming): {json.dumps(payload_for_log, indent=2)}")

        try:
            r = requests.post(self.chat_endpoint, json=payload, timeout=120)
        except requests.RequestException as e:
            print(f"[error] request failed: {e}")
            return

        if r.status_code != 200:
            print(f"[error] {r.status_code}: {r.text}")
            logger.error(f"Chat response error: {r.status_code} - {r.text}")
            return

        resp = r.json()
        logger.info(f"Chat response (non-streaming): {json.dumps(resp, indent=2)}")
        mode = r.headers.get("content-type")

        if mode == "application/json":
            self.handle_single_response(resp)
        elif mode == "text/event-stream":
            # Legacy stream mode (JSON with events array)
            self.handle_stream_response(resp)
        else:
            print("[warn] unknown response mode; raw payload:")
            print(json.dumps(resp, ensure_ascii=False, indent=2))

    def handle_command(self, cmd: str) -> bool:
        """
        Handle slash commands.
        Returns True if the command was handled, False otherwise.
        """
        if cmd == "/quit":
            print("[bye]")
            self.running = False
            return True

        elif cmd == "/help":
            print(HELP_TEXT)
            return True

        elif cmd.startswith("/stream "):
            _, val = cmd.split(" ", 1)
            val = val.strip().lower()
            if val in ("on", "true", "1", "yes"):
                self.stream_flag = True
                self.save_current_config()
                print("[info] stream set to ON")
            elif val in ("off", "false", "0", "no"):
                self.stream_flag = False
                self.save_current_config()
                print("[info] stream set to OFF")
            else:
                print("[error] usage: /stream on|off")
            return True

        elif cmd.startswith("/model"):
            parts = cmd.split(maxsplit=1)
            if len(parts) == 1:
                # Show current model
                print(f"[info] Current model: {self.model}")
                print(f"[info] Available models: {', '.join(AVAILABLE_MODELS)}")
            else:
                # Set new model with validation
                new_model = parts[1].strip()
                if new_model not in AVAILABLE_MODELS:
                    print(f"[error] Invalid model: {new_model}")
                    print(f"[error] Available models: {', '.join(AVAILABLE_MODELS)}")
                else:
                    self.model = new_model
                    self.save_current_config()
                    print(f"[info] Model set to: {self.model}")
            return True

        elif cmd.startswith("/agent"):
            parts = cmd.split(maxsplit=1)
            if len(parts) == 1:
                # Show current agent
                agent_name = None
                for name, agent_id in AVAILABLE_AGENTS.items():
                    if agent_id == self.agent_id:
                        agent_name = name
                        break
                agent_display = f"{agent_name} ({self.agent_id})" if agent_name else self.agent_id
                print(f"[info] Current agent: {agent_display}")
                print("[info] Available agents:")
                for name, agent_id in AVAILABLE_AGENTS.items():
                    print(f"        {name}: {agent_id}")
            else:
                # Set new agent with validation
                new_agent = parts[1].strip()
                # Check if it's a name or a UUID
                if new_agent in AVAILABLE_AGENTS:
                    # It's a name
                    self.agent_id = AVAILABLE_AGENTS[new_agent]
                    self.save_current_config()
                    print(f"[info] Agent set to: {new_agent} ({self.agent_id})")
                elif new_agent in AVAILABLE_AGENTS.values():
                    # It's a valid UUID
                    self.agent_id = new_agent
                    self.save_current_config()
                    # Find the name if available
                    agent_name = None
                    for name, agent_id in AVAILABLE_AGENTS.items():
                        if agent_id == new_agent:
                            agent_name = name
                            break
                    display = f"{agent_name} ({new_agent})" if agent_name else new_agent
                    print(f"[info] Agent set to: {display}")
                else:
                    print(f"[error] Invalid agent: {new_agent}")
                    print("[error] Available agents:")
                    for name, agent_id in AVAILABLE_AGENTS.items():
                        print(f"        {name}: {agent_id}")
            return True

        elif cmd.startswith("/user"):
            parts = cmd.split(maxsplit=1)
            if len(parts) == 1:
                # Show current user_id
                print(f"[info] Current user_id: {self.user_id}")
            else:
                # Set new user_id - accept UUID or email
                new_user_id = parts[1].strip()

                # Check if it looks like an email
                if "@" in new_user_id:
                    # Try to lookup user by email
                    resolved_user_id = self.lookup_user_by_email(new_user_id)
                    if resolved_user_id:
                        self.user_id = resolved_user_id
                        self.save_current_config()
                        print(f"[info] User ID set to: {self.user_id} (from email: {new_user_id})")
                    else:
                        # No user lookup endpoint or not found, use email as user_id
                        print("[warn] User lookup by email not available, using email as user_id")
                        self.user_id = new_user_id
                        self.save_current_config()
                        print(f"[info] User ID set to: {self.user_id}")
                else:
                    # Validate UUID format
                    try:
                        uuid.UUID(new_user_id)
                        self.user_id = new_user_id
                        self.save_current_config()
                        print(f"[info] User ID set to: {self.user_id}")
                    except ValueError:
                        print(f"[error] Invalid user_id: {new_user_id}")
                        print("[error] User ID must be a valid UUID or email format")
                        print(f"[error] Example UUID: {USER_ID}")
                        print("[error] Example email: user@example.com")
            return True

        elif cmd == "/session new":
            self.session_id = str(uuid.uuid4())
            self.save_current_config()
            print(f"[info] new session_id -> {self.session_id}")
            return True

        elif cmd == "/session clear":
            self.clear_session()
            return True

        elif cmd == "/ids":
            self.print_ids()
            return True

        elif cmd == "/clear":
            self.clear_screen()
            return True

        elif cmd == "/commit":
            # Reuse last tools payload if present
            if not self._last_tools_payload or not self._last_run_id:
                print("[info] nothing to commit.")
                return True
            # Offer to re-confirm all confirmables in last payload
            fake_out = {"tools": self._last_tools_payload, "run_id": self._last_run_id}
            self._prompt_and_commit_tools_if_needed(fake_out)
            return True

        elif cmd == "/reload":
            self.reload_ui_components()
            return True

        elif cmd.startswith("/history"):
            parts = cmd.split(maxsplit=1)
            if len(parts) == 1:
                print("[error] usage: /history <reload|save|clear>")
                return True

            subcmd = parts[1].strip()
            if subcmd == "reload":
                self.reload_history()
            elif subcmd == "save":
                self.save_history_now()
            elif subcmd == "clear":
                self.clear_history()
            else:
                print(f"[error] unknown history command: {subcmd}")
                print("[error] usage: /history <reload|save|clear>")
            return True

        return False

    def run(self):
        """Main REPL loop."""
        print("=" * 60)
        print("  Console Chat Client")
        print("=" * 60)
        self.print_ids()
        print(HELP_TEXT)

        while self.running:
            try:
                print()  # Add newline before prompt
                msg = self.get_user_input().strip()
            except (EOFError, KeyboardInterrupt):
                print("\n[bye]")
                break

            if not msg:
                continue

            # Expand any paste placeholders before processing
            msg = self.expand_paste_placeholders(msg)

            # Handle commands
            if msg.startswith("/"):
                if not self.handle_command(msg):
                    print(f"[warn] unknown command: {msg}")
                    print("Type /help for available commands")
                continue

            # Send regular message
            self.send_message(msg)


# ----------------- Entry Point -----------------
if __name__ == "__main__":
    client = ConsoleChatClient()
    try:
        client.run()
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"[fatal] Client crashed: {e}")
        sys.exit(1)
