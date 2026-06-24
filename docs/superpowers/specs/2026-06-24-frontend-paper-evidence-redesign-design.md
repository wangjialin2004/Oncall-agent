# Frontend Paper Evidence Redesign Design

Date: 2026-06-24

## Goal

Rebuild the frontend's overall visual style around the approved C2 direction: a comfortable "paper evidence stream" interface with richer visual hierarchy, a unified system, and lower long-term eye fatigue.

The redesign must preserve the existing React/Vite product behavior. The primary change is visual: background color, typography, surface hierarchy, panel treatment, and state styling across the login page, app shell, chat workspace, agent process panel, and service baseline manager.

## Approved Direction

Use **C2: paper surface + quiet command rails**.

- The center workspace becomes a light paper-like reading surface.
- The left navigation and right agent-process panel use restrained graphite rails, not heavy black-blue.
- Blue is reserved for primary signal and selected states.
- Warm orange/red is reserved for alert, error, or risk states.
- Typography uses a local-first Chinese UI stack for comfort and consistency.
- The UI should feel richer through layout, layered surfaces, evidence-stream rhythm, and subtle texture, not through loud glow effects.

## Scope

In scope:

- `frontend/src/styles.css` visual tokens and component styling.
- `frontend/index.html` metadata/font hints if needed.
- Component class usage only when required to support the new visual structure.
- Login page visual treatment.
- Sidebar navigation, session list, user/footer controls.
- Chat header, messages, empty state, composer, mode selector, send/stop controls.
- Agent process panel hierarchy, timeline/evidence cards, report, error, feedback states.
- Service baseline manager layout, tables, forms, empty/loading/error states.

Out of scope:

- Backend API behavior.
- Authentication behavior.
- SSE/streaming protocol behavior.
- Conversation persistence logic.
- New product features such as dashboards, theme switching, charts, or settings.
- Large React rewrites unless the existing markup blocks the approved visual system.

## Visual System

Palette:

- Page paper: `#fbfaf6`
- Secondary paper: `#f4f0e7`
- Main surface: `#fffdf8`
- Graphite rail: `#2c3440`
- Graphite deep: `#1f2630`
- Ink text: `#202936`
- Muted text: `#667085`
- Fine border: `#e5ded2`
- Primary signal: `#2f6df6`
- Soft signal: `#eaf0ff`
- Success: `#2f9d75`
- Running/warning: `#d98324`
- Error/risk: `#d95f44`

Typography:

- UI font stack: `"HarmonyOS Sans SC", "MiSans", "Microsoft YaHei UI", "PingFang SC", "Inter", system-ui, sans-serif`.
- Mono/data stack: `"JetBrains Mono", "Cascadia Code", "SFMono-Regular", Consolas, monospace`.
- Use calmer weights: 500/600 for most UI labels, 700 only for primary headings or strong states.
- Keep letter spacing at `0`; do not use negative tracking.
- Message text should read comfortably at `14px-15px` with generous line height.
- Numeric tables, trace IDs, metric values, and report/code blocks use tabular mono treatment.

Layout signature:

- Keep the three-column desktop shell: sidebar, chat workspace, agent process panel.
- Make the center workspace feel like a paper reading desk.
- Make the right panel feel like an operational evidence ledger.
- Use fine rules, soft panels, and compact grouped rows instead of nested glowing cards.
- Keep controls dense enough for an operations tool, but with more breathing room than the current dark console.

## Component Design

### Login

The login screen becomes a quiet paper entry surface over a graphite background. The form should feel professional and calm, with the product mark and submit button using the primary blue signal.

Required behavior stays unchanged:

- Username/password fields.
- Inline login/auth/network errors.
- Disabled/loading submit state.

### App Shell And Sidebar

The shell keeps the current navigation/workspace/process structure.

- Sidebar background uses graphite rail colors.
- Active navigation uses a pale blue inset or border rather than bright glow.
- Session rows should be easy to scan, with subtle hover and selected states.
- Logout and user footer remain visually secondary.

### Chat Workspace

The chat workspace becomes the main paper surface.

- Header uses a slim, quiet paper toolbar with clear title and mode selector.
- Empty state should be left-readable or centered without feeling like a dark void.
- User messages use soft blue surfaces; assistant messages use white or pale paper surfaces.
- Markdown headings, tables, code, and blockquotes must remain readable.
- Composer is fixed at the bottom with a clear input boundary and blue send action.
- Stop state remains visible when streaming.

### Agent Process Panel

This panel is the memorable C2 element.

- Treat it as a dark graphite evidence rail with paper-like nested evidence entries.
- Route, mode, status, timeline, tool calls, report, error, and feedback must scan as one diagnostic trail.
- Status pills should use restrained fills and clear labels.
- Timeline entries use compact vertical rhythm, left markers, and readable detail disclosure.
- Reports and trace details use mono typography, but avoid overpowering the main answer.

### Service Baseline Manager

The baseline manager should feel like a reliability register.

- Keep the service list/detail split.
- Use paper surfaces and fine dividers for the detail area.
- Environment badges and active service rows follow the same blue signal system.
- Tables use tabular numeric alignment and calm hover states.
- Forms use the shared input/button styling from login and composer.

## Responsive Behavior

Desktop:

- Three columns remain visible.
- The right process panel must stay wide enough for timeline and trace details.

Medium width:

- Sidebar and main workspace remain primary.
- Process panel may move below the workspace if width is constrained.

Mobile:

- Chat workspace takes priority.
- Sidebar may hide/collapse as it does today.
- Composer remains usable without horizontal overflow.
- Text must not overlap, clip, or force sideways scrolling.

## Error Handling

No data or network error semantics change.

- Login errors remain inline.
- Stream errors remain attached to the assistant message and process panel.
- Baseline manager errors remain visible in the manager surface.
- Empty/loading states should be visually calmer but still explicit.

## Implementation Notes

- Prefer token-driven CSS changes first.
- Only touch React components when markup or class names are needed to express the approved visual structure.
- Do not add new product workflows.
- Do not add decorative gradient orbs, bokeh blobs, or heavy glow backgrounds.
- Avoid a one-note beige UI by balancing paper colors with graphite rails, blue signal, and restrained warm status colors.
- Keep cards at 8px radius or less where possible, except existing larger shells if needed for continuity.

## Verification

Required commands:

- `cd frontend && npm run build`
- `cd frontend && npm test` if practical after build succeeds.

Required visual checks:

- Login page.
- Empty chat workspace.
- Chat workspace with messages if a local scenario is available.
- Agent process panel idle/running/completed/error states where possible.
- Service baseline manager.
- Desktop and one narrow/mobile viewport.

Acceptance criteria:

- Background is no longer the current black-blue console.
- Font stack is updated for more comfortable Chinese UI rendering.
- The app reads as one unified C2 visual system.
- Long text and operational evidence remain readable.
- No mobile text overlap or horizontal overflow.
- Existing product behavior remains intact.

## Risks

- The current stylesheet is centralized and large, so broad changes should remain token-driven.
- Existing worktree contains unrelated backend changes; implementation must not revert or modify them.
- Some UI copy in the current code has mojibake. If touched during this visual pass, visible copy should be corrected rather than preserved incorrectly.
