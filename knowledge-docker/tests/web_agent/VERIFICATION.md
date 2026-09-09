# Deployed browser verification — 2026-09-08

Branch: `codex/platform-v2`, worktree `.worktrees/platform-v2`.
Chromium runs in the existing pinned Playwright 1.63.0 Docker image. Application
image deployed by root before acceptance:
`sunny-knowledge/web:v2-local` at
`sha256:b268537c0c82280111bd642b0f7ef39bfa9a717c9b62b8780460c837643ea4fb`.
Gateway, Keycloak, IAM, auth, knowledge, Agent, Agent worker, LLM and channel were
actual deployed services. No Playwright route mocking or browser API replacement
was used.

## Results

- Ordinary Web: **1/1 PASS**, case **21.1 seconds**, total **22.5 seconds**.
  Log: `artifacts/web-agent-browser-verified.log`.
  Machine-readable evidence: `artifacts/web-agent/report.json` and `behavior.json`
  in `artifacts/web-agent/`.
- Channel result: **1/1 PASS**, case **8.3 seconds**, total **9.1 seconds**.
  Log: `artifacts/web-channel-result-browser.log`.
  Machine-readable evidence: `artifacts/web-agent/channel-report.json` and
  `artifacts/web-agent/channel-behavior.json`.
- Root's gated actual channel/auth/IAM/Agent/LLM/knowledge HTTP and local WeCom
  WebSocket protocol chain completed after browser release: **1/1 PASS,
  61.984 seconds** (`artifacts/channel-http-browser-gated.log`).
- All **7** ordinary-test asset records, including development failures, report
  successful exact cleanup. Each model was retired, each created Agent unpublished,
  each created session cleared, each private test space's read/write grants removed,
  and each used streaming gate released. Immutable fixture source/revision/audit
  records remain intentionally. No unrelated assets or grants were modified.

The ordinary case verified real Authorization Code + S256 PKCE, source preview and
sync/review publication, model tool/stream probes, browser Agent creation and CAS
publication, selected fixed model/scope/get tool, a real session and run, a visible
validated fact before the model's completion gate was released, original tool
names, and actual Last-Event-ID continuation after page reload. It then verified
the exact-run citation API, the ordinary protected source drawer, an actual browser
Markdown download, feedback with its independent scope, and the real channel
configuration modal without supplying credentials. Source-only revocation removed
the open answer and original source, denied citation/export reads, and remained
hidden after reload.

The channel case used a short-lived, already bound group run from root's actual
integration. Observed application traffic consisted only of `/api/v1/me` and that
run's GET, SSE, exact-run citation and export paths. It issued no write calls and
did not read ordinary sessions, history, Agent configuration, spaces, source
snapshots or Wiki pages. Deep citation links and Markdown export stayed bound to
the run. Releasing the fixture allowed root's real revocation/cleanup to continue;
the open browser answer, citation and export control disappeared.

## Visual evidence

PNG files in ignored `artifacts/web-agent/`:

- `agent-published.png`: actual fixed configuration and publication.
- `answer-prefix.png`: validated fact visible before provider completion.
- `answer-citation.png`: ordinary answer and pinned original evidence.
- `channel-editor-unconfigured.png`: actual channel modal with blank BotSecret.
- `answer-revoked.png`: ordinary result hidden after source revocation.
- `channel-readonly-citation.png`: protected channel result and run-bound evidence.
- `channel-result-revoked.png`: channel content removed after root revocation.

Final captures use Playwright `animations: 'disabled'` to finish finite transitions
before taking the image. Both actual ordinary and channel pages passed the DOM
check that document width does not exceed the viewport before opening a drawer.
The final ordinary and channel citation PNGs were visually inspected: the drawer
is opaque at its full 660-pixel width with no underlying text showing through.
Earlier transparent/translated drawer images were transition frames, not evidence
of a persistent layout defect. No product CSS change was needed.

## Development failures retained

The first five ordinary runs stopped on test-side assumptions: clicking the narrow
AntD Select input instead of its actual keyboard interaction; waiting for an
unrendered virtual-list row; confusing a closing popup with the active combobox;
expecting earlier tool stages to remain after cursor-only reload; and treating the
channel Modal as an AntD Drawer. Their original `web-agent-browser*.log` files are
retained. Final selectors follow the current combobox's actual active descendant,
use bounded keyboard navigation, distinguish the real modal/drawer shapes, and
check old tool stages before reload. No forced clicks, hidden DOM assignment,
API response substitution, weakened permission assertions or product changes were
introduced to make these cases pass. The sixth complete run passed in 27.0 seconds;
the last run above also checks stable captures and horizontal bounds.

## Scope limits

Model text is explicitly generated by `agent-model-provider`'s deterministic
protocol fixture. WeCom transport is root's local WebSocket simulator. These results
do **not** establish real Qwen quality, real tenant Bot connectivity, attachment
support, performance at 100k fragments, recovery, or full platform acceptance.
Browser login is real; browser proof-based account binding is not exercised here
(root's HTTP chain exercises the actual two-way challenge). Passwords, provider
keys, BotSecrets, binding proofs, OAuth callbacks and bearer tokens are absent from
the retained screenshots and behavior metadata. Traces/video/storage state are not
recorded.

The associated Web implementation was independently reviewed and passed the final
**118/118 Vitest tests in 19 files, 0 skipped, 150.42 seconds**, with typecheck,
lint and build successful. That component result is separate from these real
Chromium runs; see `web/AGENT_UI.md` for the exact review fixes and prior timeout
records.
