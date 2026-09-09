# First-use evidence guide

The `/onboarding` page connects the existing model, space, source, review, Wiki,
Agent and chat pages. The header and account settings link back to it. The guide
does not publish knowledge, grant worker permissions, mutate a task, or invoke a
model. Selection is optional: ordinary users can use models registered by an
administrator without access to model administration.

## Implementation checklist

- [x] Six steps with real navigation and current API evidence.
- [x] Safe ordinary-user Chat discovery; administrator-only model diagnostics.
- [x] Current space/source version, real task and exact Wiki publication matching.
- [x] Canonical lifecycle eligibility and a referenced, completed Web answer.
- [x] Actor-scoped opaque-ID restoration, authentication-session isolation and
      an explicit local reset after a selected resource becomes inaccessible.
- [x] Bounded authorization reads, focus/visibility withdrawal and late-response fencing.
- [x] Contract, hook and DOM tests, including independently reproduced findings.
- [ ] Independent final review and real-browser acceptance of this new page.

## Evidence and completion

Every aggregate starts and ends with `/me`. The actor and authorization epoch
must match; the initial actor must also match the page's authenticated principal.
Malformed responses, authorization failures, dependency failures, epoch changes,
or the total read deadline discard the entire aggregate.

1. `/agents/models?limit=50` supplies the ordinary user's safe Chat catalogue.
   A current active configuration must have a passed probe and Chat, tool and
   streaming capabilities. Only a freshly verified `platform_admin` principal
   requests `/models?limit=50`. Provider identifiers prefixed
   `protocol-fixture-` are labelled simulated. No credential, raw source body,
   answer body or provider endpoint is retained by the guide.
2. `/spaces` and the selected `/spaces/{id}` establish current content access.
   Administrative permission alone never completes the space step. Existing
   space settings and source controls perform all actual grants and mutations.
3. The selected `/sources/{id}` must be active and have a current preview with
   files. The guide retains version/count metadata, not configuration or uploads.
4. `/tasks/{latest_task_id}` must match the source, space, current source version
   and `sync` operation. A succeeded task with no selected Wiki completes the
   batch step. If a Wiki is selected, its actual publication must match the
   selected task item and be eligible: a published item's `revision_id` must
   equal the current revision, or a review item's `proposal_id` must equal the
   current review revision's `proof.proposal_id`. Missing legacy proof remains
   unverified. A `review_needed` task keeps its original collection status;
   approval of this exact selected Wiki permits continuing, while the page warns
   that other batch entries may still await review. An older same-commit Wiki
   cannot count as approval of a pending retirement proposal.
5. `/pages/{id}` must identify the current revision and selected space; its
   evidence must refer to the chosen source's current preview revision.
   `/pages/{id}/lifecycle?revision_id=...` is decoded with the existing strict
   lifecycle decoder. Server `validity.eligible`, current valid state and active
   source are required. The guide does not compute validity from the browser
   clock or treat freshness/access as validity. This GET records no Wiki visit.
6. `/sessions`, the selected session's history and `/runs/{id}` supply current
   authorized Web results. A complete, visible Web run must include the selected
   space and a nonempty fact/inference referencing an actual citation matching
   the current Wiki revision and source revision. Partial, hidden, channel and
   loose/unreferenced citation results do not complete the step. The guide
   retains only the result ID, status and matching citation count.

There is no ordinary-user endpoint proving the configured retrieval runtime's
Embedding/Reranker readiness. The guide explicitly leaves this unknown. A cited
answer does not mark those individual capabilities independently tested, nor
does a protocol-fixture probe certify real Qwen quality.

## Authorization, restoration and bounds

- API revalidation runs every five seconds while visible and focused. Normal
  slow reads coalesce, retaining the prior aggregate only within a bounded read.
  Every aggregate has a ten-second **total** deadline, including token waits.
  Timeout aborts and fences transports even if they ignore cancellation.
- Blur/hidden events immediately remove private space/source/task/Wiki/session
  metadata. Focus, visibility return and explicit refresh withdraw old metadata
  before loading fresh evidence. Unmount/selection changes reject late results.
- Principal ID plus OIDC issuer/subject/session identity key the page instance.
  A session switch removes the old instance synchronously. On an actor change,
  the former actor's URL IDs are not copied into the new actor's stored choices.
  The new actor may restore their own saved IDs only after fresh API checks.
- `sessionStorage` stores only five bounded opaque IDs under
  `sunny:onboarding:{principal_id}`. Query parameters provide reload/navigation
  restoration. No local success flags or private labels are trusted or saved.
  “清除本次选择” resets only those IDs and reads the authorized catalog again;
  it does not retry an inaccessible selected resource or grant permission.
- IAM's unpaginated space response accepts at most 10,000 entries. Paginated
  catalogues request 50 and disclose when more results exist. Task results
  accept at most 200,000 entries (including removal work), display the first
  100 plus the selected item, and preserve valid canonical paths up to 2,048
  characters. The guide links to the full existing catalogue/task pages.

## Verification record

Tests were written before implementation. Initial missing API/hook/page RED
results and later provenance, large-task and recovery RED results are preserved
under `knowledge-docker/artifacts/web-onboarding-*.log`.

Independent review reproduced three failures: authentication-session metadata
retention, an expired Wiki marked usable, and a pending same-commit retirement
mistaken for current approval. The original three assertions now pass; the
expired-page probe only gained the newly consumed authoritative lifecycle
response. Additional owner tests cover actor storage isolation, a denied-choice
reset, exact lifecycle revision, publication IDs, and legacy unknown results.

Final counts and whole-Web checks are recorded after the freeze; no deployment,
real-browser acceptance, real Qwen quality or real WeCom integration is claimed
for this page by unit tests.
