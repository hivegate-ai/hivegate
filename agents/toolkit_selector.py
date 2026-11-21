import logging
from collections.abc import Mapping, Sequence
from typing import Optional

from agno.tools import tool

from toolkits.base import BaseToolkit
from toolkits.calendar import CalendarToolkit
from toolkits.contacts import ContactsToolkit
from toolkits.drive import DriveToolkit
from toolkits.email import EmailToolkit


def create_datetime_resolver_tool(timezone_str: str):
    """
    Factory function that creates a timezone-aware datetime resolver tool.

    Args:
        timezone_str: IANA timezone identifier (e.g., "America/New_York", "Asia/Jerusalem")

    Returns:
        A tool function that resolves natural language datetime to ISO8601 format
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import dateparser

    @tool(name="resolve_datetime")
    def resolve_datetime_nlp(text: str) -> Optional[str]:
        """Convert natural language datetime to ISO8601 format using user's timezone"""
        tz = ZoneInfo(timezone_str)
        dt = dateparser.parse(  # type: ignore[no-untyped-call,misc]
            text,
            settings={
                "TIMEZONE": timezone_str,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": datetime.now(tz),
            },
        )
        return dt.isoformat() if dt else None

    return resolve_datetime_nlp


def create_multi_toolkit_selector(
    user_id: str,
    timezone: str,
    base_tools: list,
    tools_filter: Optional[list] = None,  # NEW: Filter which toolkits to include
    # Calendar toolkits (optional - None means not enabled via available_tools tag)
    calendar_google: Optional[CalendarToolkit] = None,
    calendar_microsoft: Optional[CalendarToolkit] = None,
    calendar_no_auth: Optional[CalendarToolkit] = None,
    # Contacts toolkits (optional - None means not enabled via available_tools tag)
    contacts_google: Optional[ContactsToolkit] = None,
    contacts_microsoft: Optional[ContactsToolkit] = None,
    contacts_no_auth: Optional[ContactsToolkit] = None,
    # Drive toolkits (optional - None means not enabled via available_tools tag)
    drive_google: Optional[DriveToolkit] = None,
    drive_microsoft: Optional[DriveToolkit] = None,
    drive_no_auth: Optional[DriveToolkit] = None,
    # Email toolkits (optional - None means not enabled via available_tools tag)
    email_google: Optional[EmailToolkit] = None,
    email_microsoft: Optional[EmailToolkit] = None,
    email_no_auth: Optional[EmailToolkit] = None,
    toolkit_candidates: Optional[Mapping[str, Sequence[BaseToolkit]]] = None,
):
    """
    Create a pre-hook function that dynamically selects toolkits based on OAuth token availability.

    This factory function creates an agent pre-hook that intelligently selects the appropriate
    toolkit provider (Google, Microsoft, or no-auth) for each service based on available OAuth
    tokens. The selection happens at runtime before each agent run, allowing seamless switching
    between providers without recreating the agent.

    The function performs a batch token lookup (single database query) for all integration keys,
    then selects the appropriate toolkit for each service:
    - Google toolkit if google_* token exists
    - Microsoft toolkit if microsoft_* token exists
    - No-auth toolkit (returns auth-required cards) if no tokens exist

    Services checked: Calendar, Contacts, Drive, Email

    Toolkit configuration is cached per-agent instance to avoid redundant set_tools() calls
    when the token availability hasn't changed.

    Args:
        user_id: User identifier for OAuth token lookup in the database
        timezone: IANA timezone identifier (e.g., "America/New_York", "Asia/Jerusalem")
            used for datetime resolution tool
        base_tools: List of base tools (base) to preserve when updating agent tools.
            These tools are combined with dynamically selected toolkits.
        tools_filter: Optional list of toolkit names to include (e.g., ["calendar", "email"]).
            If None, all available toolkits are included. Toolkit names are case-insensitive.
        calendar_google: Pre-initialized Google Calendar toolkit instance
        calendar_microsoft: Pre-initialized Microsoft Calendar toolkit instance
        calendar_no_auth: Pre-initialized no-auth Calendar toolkit (returns auth cards)
        contacts_google: Pre-initialized Google Contacts toolkit instance
        contacts_microsoft: Pre-initialized Microsoft Contacts toolkit instance
        contacts_no_auth: Pre-initialized no-auth Contacts toolkit (returns auth cards)
        drive_google: Pre-initialized Google Drive toolkit instance
        drive_microsoft: Pre-initialized Microsoft OneDrive toolkit instance
        drive_no_auth: Pre-initialized no-auth Drive toolkit (returns auth cards)
        email_google: Pre-initialized Gmail toolkit instance
        email_microsoft: Pre-initialized Outlook Mail toolkit instance
        email_no_auth: Pre-initialized no-auth Email toolkit (returns auth cards)
        toolkit_candidates: Further toolkits by name, each an ordered candidate list. The first
            candidate that is unauthenticated or whose integration has a token is used, so put a
            connection-prompt fallback last.

    Returns:
        Callable[[Agent, Any], None]: Pre-hook function that accepts (agent, run_input) and
        updates the agent's tools list based on current token availability. The hook stores
        a cache on the agent instance (_cached_toolkit_names) to detect configuration changes.

    Example:
        >>> calendar_google = CalendarToolkit(user_id="user123", ...)
        >>> calendar_microsoft = CalendarToolkit(user_id="user123", ...)
        >>> # ... create other toolkit instances ...
        >>> hook = create_multi_toolkit_selector(
        ...     user_id="user123",
        ...     timezone="America/New_York",
        ...     calendar_google=calendar_google,
        ...     calendar_microsoft=calendar_microsoft,
        ...     # ... other toolkit arguments ...
        ... )
        >>> agent = Agent(pre_hooks=[hook], ...)
        >>> # Agent will automatically select appropriate toolkits before each run
    """
    toolkit_candidates = toolkit_candidates or {}
    candidate_keys = [
        toolkit.service_name for candidates in toolkit_candidates.values() for toolkit in candidates if toolkit.auth
    ]

    def select_toolkits_hook(agent, run_input):
        """Pre-hook that dynamically selects toolkits based on token availability."""
        logging.info(f"[Pre-hook] Checking token availability for user {user_id}")

        # Use agent object to store per-agent cache (ensures isolation between agents)
        if not hasattr(agent, "_cached_toolkit_names"):
            agent._cached_toolkit_names = []  # type: ignore[attr-defined]

        # Build toolkit list (empty initially - toolkits added conditionally)
        toolkits: list = []

        # Import batch check from routes (to avoid circular imports)
        from api.routes.v2.agents import has_access_tokens_batch

        # Batch check all tokens in a SINGLE database query (much faster than individual checks)
        all_integration_keys = [
            "google_calendar",
            "microsoft_calendar",
            "google_contacts",
            "microsoft_contacts",
            "google_drive",
            "microsoft_drive",
            "google_gmail",
            "outlook_mail",
            *candidate_keys,
        ]
        token_status = has_access_tokens_batch(user_id, all_integration_keys)
        logging.debug(f"[Pre-hook] Token status: {token_status}")

        # Helper function to add toolkit based on token existence
        def add_toolkit_by_token(
            google_service: str,
            microsoft_service: str,
            google_toolkit,
            microsoft_toolkit,
            no_auth_toolkit,
            toolkit_name: str,
        ):
            # Skip if all toolkits are None (not enabled via available_tools tag)
            if google_toolkit is None and microsoft_toolkit is None and no_auth_toolkit is None:
                logging.debug(f"[Pre-hook] {toolkit_name} toolkit not enabled (not in available_tools tag)")
                return

            # Check if toolkit is in the filter (if filter is provided)
            if tools_filter is not None:
                # Normalize toolkit names to lowercase for case-insensitive comparison
                toolkit_name_lower = toolkit_name.lower()
                filter_lower = [t.lower() for t in tools_filter]
                if toolkit_name_lower not in filter_lower:
                    logging.debug(f"[Pre-hook] {toolkit_name} toolkit filtered out (not in tools_filter)")
                    return

            # Check token existence from batch results
            has_google_token = token_status.get(google_service, False)
            has_microsoft_token = token_status.get(microsoft_service, False)

            # Add appropriate toolkit based on token existence
            if has_google_token and google_toolkit is not None:
                logging.debug(f"[Pre-hook] User has {google_service} token, adding Google {toolkit_name} toolkit")
                toolkits.append(google_toolkit)
            elif has_microsoft_token and microsoft_toolkit is not None:
                logging.debug(f"[Pre-hook] User has {microsoft_service} token, adding Microsoft {toolkit_name} toolkit")
                toolkits.append(microsoft_toolkit)
            elif no_auth_toolkit is not None:
                logging.debug(f"[Pre-hook] No {toolkit_name} tokens found, adding no-auth toolkit")
                toolkits.append(no_auth_toolkit)

        # Add Calendar toolkit (if enabled)
        add_toolkit_by_token(
            "google_calendar", "microsoft_calendar", calendar_google, calendar_microsoft, calendar_no_auth, "Calendar"
        )

        # Add Contacts toolkit (if enabled)
        add_toolkit_by_token(
            "google_contacts", "microsoft_contacts", contacts_google, contacts_microsoft, contacts_no_auth, "Contacts"
        )

        # Add Drive toolkit (if enabled)
        add_toolkit_by_token("google_drive", "microsoft_drive", drive_google, drive_microsoft, drive_no_auth, "Drive")

        # Add Email toolkit (if enabled)
        add_toolkit_by_token("google_gmail", "outlook_mail", email_google, email_microsoft, email_no_auth, "Email")

        # Add further toolkits from their ordered candidates (if enabled)
        # TODO: the four toolkits above could become candidate groups too, replacing the
        # per-provider parameters, the fixed key list and add_toolkit_by_token with this loop.
        for toolkit_name, candidates in toolkit_candidates.items():
            if tools_filter is not None and toolkit_name.lower() not in [t.lower() for t in tools_filter]:
                logging.debug(f"[Pre-hook] {toolkit_name} toolkit filtered out (not in tools_filter)")
                continue
            selected = next((t for t in candidates if not t.auth or token_status.get(t.service_name, False)), None)
            if selected is not None:
                logging.debug(f"[Pre-hook] Adding {selected} for {toolkit_name}")
                toolkits.append(selected)

        # Combine base tools with toolkits
        all_tools = base_tools + toolkits

        # Get current toolkit string representations for comparison (use str() for BaseToolkit)
        current_toolkit_names = [str(t) if hasattr(t, "__str__") else type(t).__name__ for t in toolkits]

        # Only update agent tools if the toolkit list changed
        if current_toolkit_names != agent._cached_toolkit_names:  # type: ignore[attr-defined]
            logging.info(
                f"[Pre-hook] Toolkit configuration changed, updating agent tools: {len(all_tools)} tool(s) total "
                f"({len(base_tools)} base + {len(toolkits)} toolkit(s))"
            )
            # Log base tools
            if base_tools:
                logging.info("[Pre-hook] Base tools:")
                for idx, tool in enumerate(base_tools, 1):
                    tool_str = str(tool) if hasattr(tool, "__str__") else type(tool).__name__
                    logging.info(f"[Pre-hook]   {idx}. {tool_str}")
            # Log toolkits
            if toolkits:
                logging.info("[Pre-hook] toolkits:")
                for idx, toolkit in enumerate(toolkits, 1):
                    toolkit_str = str(toolkit) if hasattr(toolkit, "__str__") else type(toolkit).__name__
                    logging.info(f"[Pre-hook]   {idx}. {toolkit_str}")

            agent.set_tools(all_tools)
            # Update the per-agent cache
            agent._cached_toolkit_names = current_toolkit_names  # type: ignore[attr-defined]
        else:
            logging.debug("[Pre-hook] Toolkit configuration unchanged, skipping set_tools()")

    return select_toolkits_hook
