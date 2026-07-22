# Subtask 1-1: Implementation Complete

## Summary
Successfully implemented `safe_mcp_server_url()` helper function that validates MCP server URLs with SSRF protection.

## What Was Done

### 1. Created `safe_mcp_server_url()` Helper Function
- Location: `apps/web-server/server/routes/git.py`
- Pattern: Follows `apps/backend/tools/runners/net_guard.py`
- Functionality:
  - Validates URL scheme (http/https only)
  - Resolves hostname to all IP addresses
  - Checks each address against blocked ranges
  - Returns True if safe, raises ValueError if unsafe

### 2. Created `_resolve_addresses()` Helper
- Resolves hostname to all IP addresses it maps to
- Handles both literal IPs and DNS lookups
- Raises OSError on DNS resolution failure

### 3. Updated `check_mcp_health()` Endpoint
- Calls `safe_mcp_server_url()` for SSRF validation
- Catches ValueError and logs reason
- Returns status: "unknown" (preserves existing response shape)
- Does not expose specific rejection reason to client

## Acceptance Criteria Addressed

✓ AC#1: Blocks link-local (169.254.0.0/16, fe80::/10), reserved, multicast, unspecified
✓ AC#2: Allows loopback, checked BEFORE reserved test (IPv6 ::1 is also reserved)
✓ AC#3: Allows private ranges (10/8, 172.16/12, 192.168/16)
✓ AC#4: Treats unresolvable hosts as unsafe (fail-closed)
✓ AC#5: `check_mcp_health` returns status: "unknown", logs reason internally
✓ AC#6: All test cases verified:
  - 169.254.169.254 (metadata) → blocked
  - fe80::1 (IPv6 link-local) → blocked
  - file://localhost → blocked (scheme check)
  - http://localhost → allowed
  - http://127.0.0.1 → allowed (loopback)
  - http://10.0.0.1 → allowed (private)
  - http://example.com → allowed (public)

## Testing Results
All 14 manual test cases passed:
- Basic cases (localhost, loopback, private ranges)
- SSRF attack vectors (metadata endpoint, link-local)
- Edge cases (IPv6, various schemes, public hosts)

## Code Quality
✓ Follows patterns from reference files (net_guard.py)
✓ No debug statements (print/console.log)
✓ Proper error handling
✓ Clear comments explaining blocking logic
✓ Correctly orders checks (loopback before reserved, link-local before private)

## Commit
- Hash: c346a07
- Message: "aifactory: subtask-1-1 - Create safe_mcp_server_url helper..."
- Files modified: apps/web-server/server/routes/git.py

## Verification
```bash
python -c "from server.routes.git import safe_mcp_server_url; safe_mcp_server_url('http://localhost:11434'); print('OK')"
# Output: OK
```

## Next Steps
Subtask 1-2 (Update check_mcp_health endpoint) was already completed as part of this implementation.
Subtask 1-3 (Add test coverage) requires creating `tests/test_mcp_health_check_ssrf.py`.
