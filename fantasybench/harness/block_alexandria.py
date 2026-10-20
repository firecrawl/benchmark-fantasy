#!/usr/bin/env python3
"""PreToolUse hook for the "Firecrawl without Alexandria" arm. Claude Code passes the call as JSON on stdin.
  - Alexandria discovery or execution, or a search that asks for Alexandria sources: blocked (exit 2, reason on stderr).
  - Every other search: rewritten to web-only (sources without "alexandria", domainTools false) so the results do not
    advertise Alexandria tools; URL scrapes: domainTools false. Rewrites use PreToolUse "updatedInput"; the preflight checks
    that a search result in this arm carries no Alexandria tool listing.
Same API key and limits as the Firecrawl arm."""
import json, sys

call = json.load(sys.stdin)
name, args = call.get("tool_name", ""), call.get("tool_input") or {}
blocked = None
if name.endswith("firecrawl_find_tools"):
    blocked = "Alexandria tool discovery is disabled in this session."
elif name.endswith("firecrawl_scrape") and args.get("alexandria") not in (None, {}, []):
    blocked = "Alexandria execution is disabled in this session; scrape a URL instead."
elif name.endswith("firecrawl_search"):
    srcs = [s if isinstance(s, str) else (s or {}).get("type") for s in (args.get("sources") or [])]
    if "alexandria" in srcs:
        blocked = "Alexandria search is disabled in this session; search the web instead."
if blocked:
    print(blocked, file=sys.stderr)
    sys.exit(2)
new = None
if name.endswith("firecrawl_search"):
    srcs = [s for s in (args.get("sources") or []) if (s if isinstance(s, str) else (s or {}).get("type")) != "exchange"] or ["web"]
    new = {**args, "sources": srcs, "domainTools": False}
elif name.endswith("firecrawl_scrape"):
    new = {**args, "domainTools": False}
if new is not None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow", "updatedInput": new}}))
sys.exit(0)
