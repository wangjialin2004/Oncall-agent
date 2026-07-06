# Frontend Command Center Redesign Design

Date: 2026-06-23

## Goal

Redesign the React/Vite frontend into an OnCall command-center experience while preserving the existing product behavior. The redesign covers the login page, app shell, sidebar, chat workspace, agent process panel, and service baseline manager.

The implementation must also fix visible Chinese mojibake in frontend UI copy. A visually redesigned interface with unreadable Chinese text is considered incomplete.

## Chosen Direction

Use the "值班指挥台 + 证据侧栏" direction.

- Keep the three-column desktop structure: navigation, chat workspace, process/evidence panel.
- Make the center chat area quieter and easier to read.
- Make the right panel feel like an evidence stream: route, stage, tool call, timing, report, and feedback should scan as a diagnostic trail.
- Use a graphite-blue operational palette with cyan signal accents and amber running states.
- Borrow the auditability of the "证据流工作台" direction without turning the whole UI into a paper-ledger metaphor.

## Scope

In scope:

- `frontend/src/styles.css`: global tokens, layout, component styling, responsive rules.
- Frontend component copy fixes where Chinese text is currently mojibake.
- Login page visual treatment and copy.
- Sidebar navigation and session list presentation.
- Chat workspace header, empty state, messages, composer, mode control.
- Agent process panel hierarchy, timeline readability, status pills, feedback/report cards.
- Service baseline manager list/detail/table/form presentation.
- `frontend/index.html` metadata and font loading if needed.

Out of scope:

- Backend API behavior.
- Authentication flow changes.
- SSE event protocol changes.
- Conversation persistence logic changes.
- New product features such as dashboards, charts, or theme switching.
- Large component rewrites unless required to support the visual structure.

## Visual System

Palette:

- Base: graphite and blue-black surfaces for operational focus.
- Main surface: near-white or cool dark surfaces depending on region, avoiding a one-note dark-blue page.
- Accent: cyan for active routes, selected states, focus, and primary signal.
- Status: amber for running, green for completed, red for error, muted gray for idle/cancelled.
- Risk: avoid purple-heavy gradients and decorative background blobs.

Typography:

- UI font: system or existing Inter stack.
- Mono font: JetBrains Mono or existing mono stack for IDs, evidence fields, trace/span values, metrics, and reports.
- Use compact but readable sizes: small labels for metadata, clear headings for page regions, tabular numerals for metric values.

Layout signature:

- The right process panel becomes the memorable element: a dense evidence stream with clear stage grouping, compact details, and strong status markers.
- The chat area remains calm so the user can read diagnostic answers without visual noise.

## Component Design

### Login Page

The login page should feel like an entry point into an OnCall operations console. Keep one centered form, but make the brand block and supporting copy readable and professional.

Required copy fixes:

- Title: `智能 OnCall 运维平台`
- Subtitle: `Agent Gateway · 智能体运维中枢`
- Labels: `用户名`, `密码`
- Button states: `登录`, `登录中...`
- Network error: `网络错误，请检查连接后重试`

### App Shell And Sidebar

Keep the shell stable: sidebar, workspace, process panel. Improve spacing, contrast, and active states.

Sidebar copy fixes:

- `新建会话`
- `服务基线`
- `历史会话`
- `暂无历史记录`
- `未命名会话`
- `{turn_count} 轮`
- `退出登录`

### Chat Workspace

The chat workspace should prioritize readable incident diagnosis:

- Header shows title, current running state, and mode selector.
- Empty state invites the user to describe an alert or ask the knowledge base.
- User and assistant messages have distinct surfaces but avoid oversized bubbles.
- Assistant Markdown remains supported and styled for headings, lists, tables, code, and blockquotes.
- Composer is fixed at the bottom with a clear send/stop action.

Required copy fixes:

- Title: `运维助手`
- Idle subtitle: `智能 OnCall 运维平台`
- Running subtitle: `正在执行智能体推理...`
- Mode label: `模式`
- Mode options: `自动`, `知识库`
- Empty title: `运维助手已就绪`
- Empty hint: `描述一个告警事件，或向知识库提问`
- Composer label: `消息`
- Placeholder: `描述告警事件或提出运维问题...`
- Stop aria label: `停止`
- Send aria label: `发送`
- Error fallback: `（执行失败）`
- Cancel fallback: `（已取消）`

### Agent Process Panel

The process panel is the key redesign area. It should read as an evidence stream rather than a generic stack of cards.

Structure:

- Header: panel title plus current status pill.
- Route summary: route and mode.
- Timeline: compact vertical evidence stream with icons, status, stage, summaries, and optional detail disclosure.
- Report: readable mono block for final report.
- Feedback: concise adopted/corrected actions.
- Error: clear red-tinted block.

Required copy fixes include status labels, route labels, mode labels, agent labels, stage labels, detail labels, feedback labels, and fallback text. All visible Chinese strings in this component should be corrected.

### Service Baseline Manager

The baseline page should become a service reliability panel:

- Header with service baseline title and refresh action.
- Left service list with environment badges.
- Right detail panel with owner/description, baseline table, and compact form.
- Numeric columns use tabular numerals and right alignment.
- Empty/error/loading states are readable Chinese.

Required copy fixes include:

- `服务基线管理`
- `刷新`
- `新建服务`
- `加载中...`
- `暂无服务，请先新建。`
- `从左侧选择一个服务以查看/编辑基线。`
- `服务名`
- `环境`
- `归属团队`
- `描述`
- `创建服务`
- `保存中...`
- `指标`, `下限`, `上限`, `单位`, `采样窗口`
- `尚无基线，请在下方新增。`
- `新增/更新基线`
- Validation errors for empty service name and invalid numeric ranges.

## Responsive Behavior

Desktop:

- Three-column command-center layout.
- Right evidence panel should be wide enough for trace details and timeline summaries.

Medium width:

- Sidebar and chat remain side by side.
- Process panel moves below or becomes a lower region, preserving readability.

Mobile/narrow width:

- Prioritize chat.
- Hide or collapse the sidebar.
- Keep composer usable.
- Avoid text overlap and horizontal overflow.

## Error Handling

Do not change data or network error semantics. Only improve presentation and copy.

- Login network/auth errors remain inline.
- Assistant stream errors remain attached to the assistant message and process panel.
- Baseline manager errors remain a visible alert.
- Missing details or empty event data should use readable fallback text instead of mojibake.

## Testing And Verification

Required verification:

- `cd frontend && npm run build`
- Existing frontend tests if practical: `cd frontend && npm test`
- Browser visual check of:
  - Login page
  - Empty chat workspace
  - Chat workspace with messages
  - Running/idle process panel states if available
  - Service baseline manager
  - Responsive widths around desktop, medium, and mobile breakpoints

Search checks:

- Verify old mojibake is removed from visible frontend copy.
- Verify old purple-focused color remnants are not controlling the new visual identity.

## Risks

- Existing source files contain mojibake, so copy fixes must be careful and complete.
- The stylesheet is large and centralized; broad visual changes should stay token-driven where possible.
- The current worktree contains unrelated changes. Implementation must only modify frontend redesign files and must not revert unrelated files.
