---
repo: architecture
path: docs/architecture/aw-app-google-workspace-mcp.md
source: generated
edited: false
checksum: sha256:ac7f3c31c321784d156bcd41052b8e5edc67cf9b1ffa0882cc5d74eeaae7d757
---
# Google Workspace MCP

- **repo**: aw-app-google-workspace-mcp
- **layer**: app
- **technologies**: python
- **health** (derived): planned

Ports agentic-workspace's aw-google-workspace integration into aw-workspace: runs the community workspace-mcp server (taylorwilsdon/google_workspace_mcp) as a managed background service and registers it with MCP Gateway, giving every agent Gmail, Drive, Calendar, Docs, Sheets, Tasks and Contacts tools behind one Google OAuth token.

## Connections
- `http` → **aw-workspace** — routes mounted at /api/apps/google-workspace-mcp
- `stdio-mcp` → **mcp-gateway** — MCP surface aggregated by the gateway

## MCP tools
- `create_doc`
- `create_drive_file`
- `create_spreadsheet`
- `draft_gmail_message`
- `get_doc_as_markdown`
- `get_drive_file_content`
- `get_drive_shareable_link`
- `get_events`
- `get_gmail_message_content`
- `list_calendars`
- `list_contacts`
- `list_gmail_labels`
- `list_task_lists`
- `list_tasks`
- `manage_contact`
- `manage_contact_group`
- `manage_event`
- `manage_gmail_filter`
- `manage_task`
- `modify_gmail_message_labels`
- `modify_sheet_values`
- `query_freebusy`
- `read_sheet_values`
- `search_contacts`
- `search_drive_files`
- `search_gmail_messages`
- `send_gmail_message`
- `set_drive_file_permissions`
- `start_google_auth`
- `update_drive_file`

## Requirements
_none documented_
