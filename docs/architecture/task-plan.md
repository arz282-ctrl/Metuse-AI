# Task Plan

Work phase by phase. Each phase starts only after explicit user approval. A phase is complete only when its exit criteria and tests pass.

## Dependencies

Phase 0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7. Phase 3 and 4 need Phase 2 contracts; Phase 6 needs 4 (approved exports) and 5 (connections); Phase 7 needs 5.

## Phase 0 - Reconnaissance (this phase)
- 0.1 Git init, remote, `.gitignore`, `.env.example`
- 0.2 Ucuts audit and pin
- 0.3 Vendor 3 skill packs, router reference, attribution
- 0.4 `decisions.md`, this plan
- 0.5 Repository scaffold, first commit

Exit: inventory, decisions, risk list, clean history.

## Phase 1 - Runnable foundation
- 1.1 Docker Compose local stack
- 1.2 Shared contracts, migrations, RLS, dev seeds
- 1.3 Auth, tenancy, RBAC (agency and creator)
- 1.4 Storage abstraction and audit log
- 1.5 API and web shells
- 1.6 CI and tenant isolation tests

Exit: sign in, create workspace/client, isolation tests pass, local stack starts from docs. Acceptance: #1, #2.

## Phase 2 - Skill runtime, workers, Muse adapter
- 2.1 Skill registry and router
- 2.2 Muse adapter and capability registry
- 2.3 Job envelope, usage records, budgets
- 2.4 Research, writing/SEO/AEO, media-pack workers
- 2.5 API wiring and tests

Exit: workers callable via API, schema-valid, usage recorded, no secret leaks. Acceptance: #7, #8, #17, #18.

## Phase 3 - Unified video generation
- 3.1 Gateway core and state machine
- 3.2 Capability registry and routing
- 3.3 Mock provider, then one real adapter once access is verified
- 3.4 Durable polling/webhooks, asset persistence
- 3.5 API and verification

Exit: text-to-video and image-to-video end to end, restart-safe, cost recorded. Acceptance: #9, #10, #17.

## Phase 4 - Cloud editing
- 4.1 Upload and media validation
- 4.2 Timeline schema and Ucuts adapter
- 4.3 Isolated render worker and QA
- 4.4 Approval and revision workflow, review UI
- 4.5 End-to-end verification

Exit: upload -> preview -> revision -> final MP4; QA rejects bad output. Acceptance: #11, #12, #13.

## Phase 5 - Meta connection
- 5.1 Meta app docs and permission matrix
- 5.2 Unified Login flow and token storage
- 5.3 Eligibility, asset discovery, capability gating
- 5.4 Webhooks and connection UI
- 5.5 Verification

Exit: creator and agency test connections, personal account blocked, no plaintext tokens. Acceptance: #3, #4, #5, #6, #18.

## Phase 6 - Publishing and insights
- 6.1 Content calendar and scheduling
- 6.2 Publishing state machine and worker
- 6.3 Insights sync
- 6.4 Verification

Exit: approved content publishes, no duplicates on retry, insights attributed correctly. Acceptance: #14, #15.

## Phase 7 - Ads
- 7.1 Read-only ad discovery and insights
- 7.2 Campaign drafts and validation
- 7.3 Approved execution with audit trail
- 7.4 Full 18-test acceptance run

Exit: no mutation without explicit approval; all 18 acceptance tests pass. Acceptance: #16 plus full suite.

## Acceptance test map

1 Agency isolation (P1) - 2 Creator without ads (P1) - 3 Personal IG blocked (P5) - 4 Creator connection (P5) - 5 Agency connection (P5) - 6 Permission denial (P5) - 7 Research (P2) - 8 SEO/AEO (P2) - 9 Text-to-video (P3) - 10 Image-to-video (P3) - 11 Cloud editing (P4) - 12 Media QA (P4) - 13 Approval binding (P4) - 14 Publishing (P6) - 15 Duplicate prevention (P6) - 16 Ads safety (P7) - 17 Cost attribution (P2/P3/P4) - 18 Credential safety (P2/P5, re-run in P7)
