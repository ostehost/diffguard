# Roadmap

*Last updated: 2026-02-11*

## Vision

DiffGuard is a **verification layer** for code changes. Not a reviewer (opinions) — a verifier (facts).

- **Human hook:** "Catches the bugs that pass code review"
- **Technical hook:** "Precision verification for AI-native workflows"

Works for both humans (VS Code/Cursor devs) and AI agents (Claude Code, OpenClaw, pipelines). Local-first, privacy-first, agent-native CLI. Your code never leaves your machine.

## Competitive context

| Tool | Model | Limitation |
|------|-------|------------|
| CodeRabbit | SaaS, $15-30/seat | Code leaves your machine. Reviews on their servers. |
| Aider repo-map | tree-sitter + PageRank | Locked inside Aider. Not usable standalone. |
| ast-grep | Pattern search CLI | Searches, doesn't diff. No semantic change detection. |
| semgrep | Static analysis rules | Security-focused. Not a change reviewer. |
| GitHub Copilot review | SaaS, GitHub-only | Vendor lock-in. No local option. |
| claude-code-action | GitHub Action, runs on your runner | Broad review, not precision bug detection. **Complementary to DiffGuard** — DiffGuard triages, claude-code-action reviews what matters. |

DiffGuard is the only open-source, local-first, agent-native option.

## License

BSL 1.1 — see [LICENSE](https://github.com/oste-git/diffguard/blob/main/LICENSE) for details.

## Phases

### Phase 1: Ship it (Now → 4 weeks)

**Status: current**

**Goals:**
- Ship CLI to PyPI (`pip install diffguard`)
- Integration snippets for CLAUDE.md, Cursor rules, Aider
- Launch post

**Gate criteria:**
- 50 installs in first 30 days
- 5 distinct users in 30 days

**Kill signals:**
- <20 installs after 30 days with active promotion

### Phase 2: CI integration (Weeks 5–10)

**Status: planned**

**Goals:**
- GitHub Action (`diffguard-action`)
- `--ci` mode (non-interactive, structured output)
- Team config file (`.diffguard.yml`)
- `--fail-on` severity flag for CI gates
- claude-code-action + DiffGuard integration example (show them running together)
- "Bugs AI reviewers miss" benchmark — test DiffGuard against AI-generated code, publish results
- Ensure `--format json` output has severity, confidence, location fields for agent consumption

**Gate criteria:**
- \>100 weekly active users (WAU)

**Kill signals:**
- <50 WAU despite GitHub Action availability

### Phase 3: Watch mode (Weeks 11–16)

**Status: planned**

**Goals:**
- `diffguard watch` — daemon mode, incremental review on file save
- Context hints — suggest related files/symbols for the agent
- Rust and Java language support

**Gate criteria:**
- Sustained growth in WAU
- Community requests for daemon mode

**Kill signals:**
- No organic demand for watch mode after Phase 2 traction

## Future-Proof Thesis

> As AI generates more code, the need for automated verification increases, not decreases. Human review capacity is fixed. AI-generated code volume is exponential. The bottleneck shifts from "who writes the code" to "who verifies the code is correct." DiffGuard is a verification engine that works regardless of whether code was written by a human, Cursor, Claude Code, or a fully autonomous agent swarm. The less human oversight there is in the loop, the more critical precision-targeted bug detection becomes.

DiffGuard is infrastructure (model-agnostic pre-processor), not a model wrapper. CLI-first design is already positioned for the agentic future.

## Kill / continue signals

| Milestone | Signal | Action |
|-----------|--------|--------|
| Month 1 | >50 installs | Continue to Phase 2 |
| Month 3 | >100 WAU, >200 GitHub stars | Continue to Phase 3 |
| Month 6 | External contributors appearing | Project has legs — invest more |
| Month 1 | <20 installs | Reassess positioning or pivot |
| Month 3 | <50 WAU | Consider stopping active development |
