# 001. CLI-first, not MCP/GUI/web

**Status:** Accepted

## Context

DiffGuard needs a distribution strategy that works with AI coding agents (Claude Code, Cursor, Aider, etc.). The options are:

- **MCP server** — Model Context Protocol, requires running a server process
- **IDE plugin** — per-editor, high maintenance
- **Web dashboard** — SaaS-like, conflicts with local-first principle
- **CLI** — one binary, works everywhere

Community consensus from practitioners (steipete, Armin Ronacher, Mario Zechner) is that CLIs beat MCPs for agent tooling. One line in CLAUDE.md is enough: `Run diffguard review before committing.` No protocol negotiation, no server lifecycle, no version compatibility matrix.

## Decision

CLI-only. No MCP server, no IDE plugin, no web dashboard.

Distribution via PyPI (`pip install diffguard`). Integration via a single instruction in the agent's project context file.

## Consequences

- **Simple distribution:** `pip install diffguard` works everywhere Python runs
- **Agent-agnostic:** Works with any agent that can run shell commands
- **Low maintenance:** One interface to maintain, not N plugins
- **Trade-off:** No visual UI for non-technical users — but they're not the target audience
