# Codex Adapter

This is a Codex adapter, not the repository's policy document.

1. Read [AGENTS.md](./AGENTS.md) before investigating, planning, changing, or reviewing work in this repository.
2. Treat `AGENTS.md` as the complete source of repository workflow, safety, architecture, testing, and documentation requirements.
3. Use Codex host capabilities—such as skills, plans, workspace tools, and collaboration features—only when they remain within `AGENTS.md` and the active host/user instructions.
4. Preserve the code-discovery order defined in `AGENTS.md`; use the codebase knowledge graph before text search when it is available.
5. If this file conflicts with `AGENTS.md`, follow `AGENTS.md`; higher-priority host/system and explicit user instructions still take precedence.

## Current Working Role

Unless the user explicitly requests implementation, operate primarily as a planning and review partner:

1. Write executable plans covering the problem, design decisions, scope, risks, verification, and rollback path.
2. Review code with findings first, prioritizing correctness, regressions, security, contract changes, and test gaps; include concrete file and line references.
3. Evaluate system direction and design with attention to module boundaries, data flow, compatibility, observability, degradation paths, and long-term maintenance cost.
4. Do not modify code by default when the request is for analysis, planning, review, or architectural guidance.

Keep Codex-specific guidance here. Do not copy project roadmaps, plan indexes, product red lines, or architecture rules into this file.
