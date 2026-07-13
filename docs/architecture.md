# Architecture

`RoutingEngine`, `StateMachine`, `FailureClassifier`, `FingerprintEngine`, `ProgressEvaluator`, and `PolicyEvaluator` are pure deterministic components. They do not run Codex, inspect global processes, mutate Git, execute validation, or read mutable configuration after run creation. A model's diagnosis and suggested next action are evidence, never state transitions.

Side effects are represented by protocols and implemented by Codex CLI, subprocess, Git, SQLite, filesystem, validation, review, clock, and event adapters. The operation journal records a planned external side effect before execution and records completion only after durable evidence exists. SQLite transactions couple transitions to the durable run state; terminal transitions update state and status atomically. Capability, event, Git-snapshot, validation, routing, review, policy, usage, artifact, and correlation metadata are queryable without placing large patches or logs in the database.

Each attempt is a fresh top-level `codex exec`. Attempts in one run share the same detached worktree so a stronger tier can retain, repair, revert, or replace prior changes. Controller evidence stays under the external state root. External validation—not model prose—is the acceptance authority.

The explicit state table rejects illegal transitions. Failures are classified as success, substantive, transient, environment, permissions, credentials, configuration, specification, policy, stalled, cancelled, or unknown. Transient retries have a separate budget; stop classes never escalate into a more expensive model.

Max is the serial deep route for one tightly coupled problem; Ultra is the parallel deep route and requires explicit deterministic metadata. Exactly one is chosen. Unsupported logical efforts use only configured same-model fallbacks after a Git snapshot proves the failed invocation did not mutate the candidate.

Recovery is conservative. PID plus process-start identity protects locks against PID reuse. An interrupted model attempt is captured, not replayed. Only validators explicitly marked safe may rerun. Acceptance after recovery still requires path/Git policy, any required read-only review, and a mutation-free final validation. Unknown or non-idempotent state goes to human review.
