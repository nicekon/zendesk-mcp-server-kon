# Zendesk Support Read Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe read-only ticket, ticket-list, and conversation tools on the common Zendesk transport.

**Architecture:** `tools/tickets.py` owns ticket endpoint paths and result normalization; `server.py` only registers schemas and dispatches to that module. Every call returns the existing JSON envelope, checks configuration before constructing a client, and makes no write request.

**Tech Stack:** Python 3.10–3.12, httpx, MCP SDK, pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-unified-zendesk-mcp-design.md`

## Global Constraints

- Read-only tools never send POST, PUT, PATCH, or DELETE requests.
- Tool names are canonical `zendesk_*`; no legacy alias is registered.
- Ticket IDs are positive integers; invalid values return `validation_error` before network access.
- List responses use `items`, `has_more`, `next_cursor`, and `truncated` rather than raw Zendesk payloads.
- Customer text is returned as `untrusted_user_content`; logs remain metadata-only.

---

### Task 1: Ticket read service

**Files:**
- Create: `src/zendesk_mcp_server/tools/__init__.py`
- Create: `src/zendesk_mcp_server/tools/tickets.py`
- Create: `tests/test_tickets.py`

**Interfaces:**
- Produces `TicketTools(client: ZendeskClient | None)`.
- Produces `get_ticket(ticket_id: int) -> dict[str, object]`, `list_tickets(limit: int = 100) -> dict[str, object]`, and `get_conversation(ticket_id: int) -> dict[str, object]`.

- [ ] **Step 1: Write failing tests**

```python
def test_get_ticket_rejects_zero_without_a_client():
    result = TicketTools(None).get_ticket(0)
    assert result["error"]["code"] == "validation_error"


def test_list_tickets_normalizes_cursor_response(client):
    result = TicketTools(client).list_tickets(limit=2)
    assert result["items"] == [{"id": 1}]
    assert result["has_more"] is True
    assert result["next_cursor"] == "next"
```

- [ ] **Step 2: Run red**

Run: `uv run pytest tests/test_tickets.py -v`

Expected: FAIL because `tools.tickets` is absent.

- [ ] **Step 3: Implement minimum read-only endpoints**

Use only `client.get`: `/api/v2/tickets/{id}.json`, `/api/v2/tickets.json`, and `/api/v2/tickets/{id}/comments.json`. Clamp list limit to 1–100. Convert Zendesk `ticket`, `tickets`, `comments`, `meta.has_more`, and `meta.after_cursor` into the standard output. Mark conversation comment bodies and HTML as `untrusted_user_content=true` in response metadata.

- [ ] **Step 4: Run green**

Run: `uv run pytest tests/test_tickets.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/zendesk_mcp_server/tools tests/test_tickets.py
git commit -m "feat: add read-only Zendesk ticket tools"
```

### Task 2: MCP schemas and dispatch

**Files:**
- Modify: `src/zendesk_mcp_server/server.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Registers `zendesk_list_tickets`, `zendesk_get_ticket`, `zendesk_get_ticket_conversation` alongside connection status.

- [ ] **Step 1: Write failing registration tests**

```python
def test_support_read_tools_are_registered():
    assert [tool.name for tool in build_tools()] == [
        "zendesk_get_connection_status",
        "zendesk_list_tickets",
        "zendesk_get_ticket",
        "zendesk_get_ticket_conversation",
    ]
```

- [ ] **Step 2: Run red**

Run: `uv run pytest tests/test_server.py::test_support_read_tools_are_registered -v`

Expected: FAIL because only connection status is registered.

- [ ] **Step 3: Implement dispatch**

Create `ZendeskClient` only for a fully configured `Settings`; otherwise return `not_configured`. Pass schema-validated numeric IDs and limit to `TicketTools`. Keep `zendesk_get_connection_status` network-free.

- [ ] **Step 4: Run green and commit**

Run: `uv run pytest tests/test_server.py tests/test_tickets.py -v`

```bash
git add src/zendesk_mcp_server/server.py tests/test_server.py
git commit -m "feat: register Support read tools"
```

### Task 3: Verify and document this pre-release surface

**Files:**
- Modify: `README.md`
- Modify: `README.ko.md`

- [ ] **Step 1: Update only the current tool list**

Document the three Support read tools and retain the statement that all writes remain unavailable.

- [ ] **Step 2: Verify and commit**

Run: `uv run pytest -v && uv build && uv run python -m compileall -q src`

```bash
git add README.md README.ko.md
git commit -m "docs: describe Support read tools"
```

## Self-Review

- Ticket/list/conversation coverage maps to the Support read requirements; search, count, export, comments, writes, attachments, metadata, Guide, CSAT, and Community remain separate phases.
- The service depends only on the existing `ZendeskClient.get` and does not add another HTTP transport.
- No task creates a callable placeholder or uses a source-text documentation test.
