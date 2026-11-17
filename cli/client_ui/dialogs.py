"""
Interactive Dialogs for CLI Client

This module contains InquirerPy-based interactive dialogs for user confirmations
and tool argument editing.
"""

import json
from typing import Any, Dict

from InquirerPy import inquirer
from InquirerPy.base.control import Choice
from InquirerPy.separator import Separator
from rich.panel import Panel
from rich.text import Text


class DialogBuilder:
    """
    Builds interactive dialogs for tool confirmations and argument editing.
    """

    def __init__(self, result_formatter):
        """
        Initialize DialogBuilder.

        Args:
            result_formatter: ResultFormatter instance for displaying Rich panels
        """
        self.result_formatter = result_formatter

    def build_dialog_for_tool(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build an InquirerPy form to edit a tool's arguments and choose an action.

        Args:
            tool: The tool object containing tool_name, args, etc.

        Returns:
            Dict with 'action' and 'edited_args' keys
        """
        tool_name = tool.get("tool_name") or tool.get("name") or "Unknown"

        # Special handling for destructive operations - simplified confirmation dialogs
        if tool_name == "cancel_meeting":
            return self._build_cancel_meeting_dialog(tool)
        elif tool_name == "delete_contact":
            return self._build_delete_contact_dialog(tool)
        elif tool_name == "delete_file":
            return self._build_delete_file_dialog(tool)
        elif tool_name == "delete_email_permanently":
            return self._build_delete_email_dialog(tool)
        elif tool_name == "send_email":
            return self._build_send_email_dialog(tool)
        elif tool_name == "send_draft":
            return self._build_send_draft_dialog(tool)
        elif tool_name == "read_file":
            return self._build_read_file_dialog(tool)

        # Default handling for other tools
        return self._build_generic_dialog(tool)

    def _build_cancel_meeting_dialog(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a simplified confirmation dialog for cancel_meeting.
        Shows event ID and asks for OK/Cancel confirmation.
        """
        tool_args = tool.get("tool_args") or tool.get("args") or {}
        event_id = tool_args.get("event_id") or tool_args.get("meeting_id") or "Unknown"

        # Display cancellation confirmation in a Rich panel
        content = Text()
        content.append("Cancel meeting with ID:\n\n", style="yellow")
        content.append(f"{event_id}\n\n", style="bold white")
        content.append("All attendees will be notified of the cancellation.", style="dim")

        self.result_formatter.console.print(
            Panel(
                content,
                title="[bold red]⚠ Cancel Meeting[/bold red]",
                title_align="left",
                border_style="red",
                padding=(1, 2),
            )
        )

        # Simple OK/Cancel confirmation
        action = inquirer.select(
            message="Confirm cancellation:",
            choices=[
                Choice(value="confirm", name="✓ OK"),
                Choice(value="cancel", name="✗ Cancel"),
            ],
            default="cancel",
        ).execute()

        # Return in the expected format
        return {
            "action": action,
            "edited_args": tool_args,
        }

    def _build_generic_dialog(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a generic InquirerPy form for editing tool arguments.

        Args:
            tool: The tool object containing tool_name, args, etc.

        Returns:
            Dict with 'action' and 'edited_args' keys
        """
        tool_name = tool.get("tool_name") or tool.get("name") or "Unknown"
        tool_id = tool.get("tool_call_id") or tool.get("id") or "?"
        tool_args = tool.get("tool_args") or tool.get("args") or {}

        print(f"\n{'=' * 60}")
        print(f"  Tool: {tool_name} (id={tool_id})")
        print(f"{'=' * 60}")

        # Build form questions for each arg
        questions = []
        for key, value in tool_args.items():
            if isinstance(value, bool):
                questions.append(
                    {
                        "type": "confirm",
                        "name": key,
                        "message": f"{key}:",
                        "default": value,
                    }
                )
            elif isinstance(value, (int, float)):
                questions.append(
                    {
                        "type": "number",
                        "name": key,
                        "message": f"{key}:",
                        "default": value,
                    }
                )
            elif isinstance(value, (list, dict)):
                # For lists and objects, show as JSON for editing (keeps nested values and nulls)
                questions.append(
                    {
                        "type": "input",
                        "name": key,
                        "message": f"{key} (JSON):",
                        "default": json.dumps(value, ensure_ascii=False),
                    }
                )
            else:
                # String or other
                questions.append(
                    {
                        "type": "input",
                        "name": key,
                        "message": f"{key}:",
                        "default": str(value) if value is not None else "",
                    }
                )

        # Collect answers
        edited_args = {}
        for q in questions:
            if q["type"] == "confirm":
                answer = inquirer.confirm(message=q["message"], default=q["default"]).execute()
            elif q["type"] == "number":
                answer = inquirer.number(message=q["message"], default=q["default"]).execute()
                # Ensure it's actually a number (InquirerPy might return string)
                if isinstance(answer, str):
                    try:
                        answer = int(answer) if "." not in answer else float(answer)
                    except ValueError:
                        pass  # Keep as string if conversion fails
            else:
                answer = inquirer.text(message=q["message"], default=q["default"]).execute()

            # Parse JSON lists and objects back
            if isinstance(tool_args.get(q["name"]), (list, dict)) and isinstance(answer, str):
                try:
                    answer = json.loads(answer)
                except json.JSONDecodeError:
                    print(f"[warn] Invalid JSON for {q['name']}, keeping as string")

            edited_args[q["name"]] = answer

        # Determine confirm button text based on tool name
        confirm_text_map = {
            "schedule_meeting": "✓ Schedule Meeting",
            "schedule_meeting_find_time": "✓ Find Available Times",
        }
        confirm_text = confirm_text_map.get(tool_name, "✓ Confirm & Submit")

        # Ask for action
        print(f"\n{'=' * 60}")
        action = inquirer.select(
            message="Choose action:",
            choices=[
                Choice(value="confirm", name=confirm_text),
                Choice(value="skip", name="⊘ Skip This Tool"),
                Separator(),
                Choice(value="cancel", name="✗ Cancel All"),
            ],
            default="confirm",
        ).execute()

        return {
            "action": action,
            "edited_args": edited_args,
        }

    # ---------------------------
    # Contacts Dialogs
    # ---------------------------

    def _build_delete_contact_dialog(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a simplified confirmation dialog for delete_contact.
        Shows contact resource name and asks for OK/Cancel confirmation.
        """
        tool_args = tool.get("tool_args") or tool.get("args") or {}
        resource_name = tool_args.get("resource_name") or "Unknown"

        # Display deletion confirmation in a Rich panel
        content = Text()
        content.append("Delete contact:\n\n", style="yellow")
        content.append(f"{resource_name}\n\n", style="bold white")
        content.append("This action cannot be undone.", style="dim red")

        self.result_formatter.console.print(
            Panel(
                content,
                title="[bold red]⚠ Delete Contact[/bold red]",
                title_align="left",
                border_style="red",
                padding=(1, 2),
            )
        )

        # Simple OK/Cancel confirmation
        action = inquirer.select(
            message="Confirm deletion:",
            choices=[
                Choice(value="confirm", name="✓ OK"),
                Choice(value="cancel", name="✗ Cancel"),
            ],
            default="cancel",
        ).execute()

        # Return in the expected format
        return {
            "action": action,
            "edited_args": tool_args,
        }

    # ---------------------------
    # Drive Dialogs
    # ---------------------------

    def _build_delete_file_dialog(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a simplified confirmation dialog for delete_file.
        Shows file ID and asks for OK/Cancel confirmation.
        """
        tool_args = tool.get("tool_args") or tool.get("args") or {}
        file_id = tool_args.get("file_id") or "Unknown"

        # Display deletion confirmation in a Rich panel
        content = Text()
        content.append("Delete file with ID:\n\n", style="yellow")
        content.append(f"{file_id}\n\n", style="bold white")
        content.append("This file will be moved to trash or deleted permanently.", style="dim")

        self.result_formatter.console.print(
            Panel(
                content,
                title="[bold red]⚠ Delete File[/bold red]",
                title_align="left",
                border_style="red",
                padding=(1, 2),
            )
        )

        # Simple OK/Cancel confirmation
        action = inquirer.select(
            message="Confirm deletion:",
            choices=[
                Choice(value="confirm", name="✓ OK"),
                Choice(value="cancel", name="✗ Cancel"),
            ],
            default="cancel",
        ).execute()

        # Return in the expected format
        return {
            "action": action,
            "edited_args": tool_args,
        }

    def _build_read_file_dialog(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a simplified confirmation dialog for read_file.
        Shows file ID and asks for OK/Cancel confirmation to index file into knowledge base.
        """
        tool_args = tool.get("tool_args") or tool.get("args") or {}
        file_id = tool_args.get("file_id") or "Unknown"

        # Display read file confirmation in a Rich panel
        content = Text()
        content.append("Read and index file into knowledge base:\n\n", style="cyan")
        content.append("📄 File ID: ", style="bold")
        content.append(f"{file_id}\n\n", style="white")
        content.append("💡 ", style="bold")
        content.append("File content will be downloaded and indexed for the agent to query.", style="dim")

        self.result_formatter.console.print(
            Panel(
                content,
                title="[bold cyan]📄 Index File into Knowledge Base[/bold cyan]",
                title_align="left",
                border_style="cyan",
                padding=(1, 2),
            )
        )

        # Simple OK/Cancel confirmation
        action = inquirer.select(
            message="Confirm file indexing:",
            choices=[
                Choice(value="confirm", name="✓ OK - Index File"),
                Choice(value="cancel", name="✗ Cancel"),
            ],
            default="confirm",
        ).execute()

        # Return in the expected format
        return {
            "action": action,
            "edited_args": tool_args,
        }

    # ---------------------------
    # Email Dialogs
    # ---------------------------

    def _build_delete_email_dialog(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a simplified confirmation dialog for delete_email_permanently.
        Shows message ID and asks for OK/Cancel confirmation.
        """
        tool_args = tool.get("tool_args") or tool.get("args") or {}
        message_id = tool_args.get("message_id") or "Unknown"

        # Display deletion confirmation in a Rich panel
        content = Text()
        content.append("Permanently delete email:\n\n", style="yellow")
        content.append(f"{message_id}\n\n", style="bold white")
        content.append("⚠ This action CANNOT be undone!", style="bold red")
        content.append("\nThe email will be permanently deleted, not moved to trash.", style="dim")

        self.result_formatter.console.print(
            Panel(
                content,
                title="[bold red]⚠ Permanently Delete Email[/bold red]",
                title_align="left",
                border_style="red",
                padding=(1, 2),
            )
        )

        # Simple OK/Cancel confirmation
        action = inquirer.select(
            message="Confirm permanent deletion:",
            choices=[
                Choice(value="confirm", name="✓ OK - Delete Permanently"),
                Choice(value="cancel", name="✗ Cancel"),
            ],
            default="cancel",
        ).execute()

        # Return in the expected format
        return {
            "action": action,
            "edited_args": tool_args,
        }

    def _build_send_email_dialog(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a custom dialog for send_email with multiline body editing.
        """
        tool_args = tool.get("tool_args") or tool.get("args") or {}

        # Display email preview in a Rich panel
        to = tool_args.get("to") or []
        subject = tool_args.get("subject") or ""
        body_text = tool_args.get("body_text") or ""
        cc = tool_args.get("cc") or []
        bcc = tool_args.get("bcc") or []

        content = Text()
        content.append("📧 Email Preview\n\n", style="bold cyan")
        content.append("To: ", style="bold")
        content.append(f"{', '.join(to) if isinstance(to, list) else to}\n", style="")
        if cc:
            content.append("CC: ", style="bold")
            content.append(f"{', '.join(cc) if isinstance(cc, list) else cc}\n", style="")
        if bcc:
            content.append("BCC: ", style="bold")
            content.append(f"{', '.join(bcc) if isinstance(bcc, list) else bcc}\n", style="")
        content.append("Subject: ", style="bold")
        content.append(f"{subject}\n\n", style="")
        content.append("Body:\n", style="bold")
        content.append(f"{body_text}\n", style="")

        self.result_formatter.console.print(
            Panel(
                content,
                title="[bold cyan]✉ Email Details[/bold cyan]",
                title_align="left",
                border_style="cyan",
                padding=(1, 2),
            )
        )

        # Ask for action without editing (editing is complex for emails)
        action = inquirer.select(
            message="Send this email?",
            choices=[
                Choice(value="confirm", name="✓ Send Email"),
                Choice(value="skip", name="⊘ Skip"),
                Separator(),
                Choice(value="cancel", name="✗ Cancel All"),
            ],
            default="confirm",
        ).execute()

        return {
            "action": action,
            "edited_args": tool_args,
        }

    def _build_send_draft_dialog(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build a confirmation dialog for send_draft.
        Shows draft ID and asks for confirmation to send.
        """
        tool_args = tool.get("tool_args") or tool.get("args") or {}
        draft_id = tool_args.get("draft_id") or "Unknown"

        # Display draft send confirmation in a Rich panel
        content = Text()
        content.append("Send draft email:\n\n", style="bold cyan")
        content.append("📝 Draft ID: ", style="bold")
        content.append(f"{draft_id}\n\n", style="white")
        content.append("The draft will be sent and removed from drafts.", style="dim")

        self.result_formatter.console.print(
            Panel(
                content,
                title="[bold cyan]📧 Send Draft Email[/bold cyan]",
                title_align="left",
                border_style="cyan",
                padding=(1, 2),
            )
        )

        # Simple Send/Cancel confirmation
        action = inquirer.select(
            message="Send this draft?",
            choices=[
                Choice(value="confirm", name="✓ Send Draft"),
                Choice(value="skip", name="⊘ Skip"),
                Separator(),
                Choice(value="cancel", name="✗ Cancel All"),
            ],
            default="confirm",
        ).execute()

        # Return in the expected format
        return {
            "action": action,
            "edited_args": tool_args,
        }
