# Architecture

Strongwiz is a model-neutral reasoning laboratory. It sits between replaceable
reasoning providers and replaceable problem domains, preserving what was
observed, what was inferred, why a proposal was selected, what an external
executor did, and what changed afterward.

The kernel is intentionally not a model, autonomous authority, or domain
solver. Models propose. Control-owned policy routes. An external executor may
act only when the execution coordinator consumes an independently supplied
grant and exact one-use permit at the call boundary. A domain adapter
interprets the resulting state and remains authoritative for its own terminal
condition.

## Declared boundary

The public boundary candidate is versioned as `strongwiz.contract.v1`. Contract values
are closed, immutable, validated, and content-addressable. Canonical
JSON excludes non-finite numbers and duplicate keys so equal evidence has one
stable representation.

The four principal contracts are:

- **Observation:** a domain, scope, epoch, raw-payload evidence reference,
  concise summary, and the action names available at that moment. An
  observation does not contain an interpretation.
- **Action:** an `ActionSpec` containing a symbolic name and JSON parameters.
  A `CandidateProposal` binds it to the current observation, goal, meaningful
  distinction, falsifiable prediction, evidence, declared costs, and concise
  rationale. A proposal is not permission to act.
- **Memory:** exact-version account headers, derived facts, mechanic versions,
  goal transitions, residual lineage, and continuation snapshots. Reuse must
  match its producer, scope, version, and epoch or pass an explicit transfer
  rule. Supersession and reopening preserve prior identity.
- **Receipt:** an immutable payload in a single-writer SQLite ledger plus a
  hash-chained `strongwiz.receipt.v1` envelope. Session and experiment receipts
  bind decisions, outcomes, limitations, and terminal disposition. A receipt
  establishes the recorded trace under its stated boundary; it does not make
  the payload true.

The SQLite ledger is the replay surface: it verifies every canonical object,
receipt occurrence, parent reference, table projection, and hash-chain link.
`export_receipt_projection_jsonl` is deliberately named as a projection because
it exports envelopes and primary payloads, not every referenced object needed
to reconstruct a complete run. `lab.pack_evidence` closes that gap for sealed
runs by exporting every object and every receipt into a portable capsule whose
manifest binds both complete projections.

## Components

`contracts` defines the cross-boundary values. `drivers` defines model, domain,
capability, and executor protocols. `runtime` enforces the scan, decision, and
assessment lifecycle. `routing` evaluates identity, witness, scope, trace,
authority, consequence, resource, and re-entry guards without executing an
action. `orchestration` binds an admitted route to the exact control snapshot,
PEA/PECAN/SEED decision, task grant, goal ID/digest pair, proposal, action,
observation ID/digest pair, scope, executor, and single-use permit. Its
`execute_once` bridge owns the writer call, returns separate release and
execution-attempt receipts inside an immutable, nonserializable coordinator-issued
result, and never exposes a reusable bare permit token.
Its writer receives a non-authorizing command with the exact invocation and
idempotency identities. Once that call begins, an exception is recorded as an
unknown effect rather than evidence that nothing happened.
The task grant explicitly classifies whether human-facing release review is
required; the coordinator derives the SEED gate from that control-owned field
or a proposal-declared `OUTPUT` effect, never from the decision being checked.

The remaining modules are replaceable reasoning services:

- `goals`, `learning`, and `policy` manage goal-relevant distinctions,
  factored prediction residuals, local repair, and fast/deep cadence;
- `facts`, `feedback`, and `accounts` retain earned facts, branch-safe
  continuation state, exact versions, and reopening handles;
- `planning` supplies bounded deterministic graph search;
- `experiments` supplies fixed-denominator retention ablations and honest
  attempt dispositions;
- `measurements` supplies canonical rational and interval quantities for
  scientific domains without binary floating-point drift;
- `authority` keeps grants external, revocable, scoped, and revalidated before
  release;
- `lab_policy` exposes the PEA, PECAN, and SEED control interfaces;
- `integrity` binds source, configuration, dependencies, model artifacts,
  adapters, capabilities, and policies into a frozen runtime manifest;
- `modelkit` binds plain local callbacks or framed offline model processes to
  exact Strongwiz requests without giving the provider control state;
- `transport` supplies bounded canonical binary frames, partial-I/O handling,
  checksums, timeouts, and a declared replay window;
- `conformance` supplies non-authorizing structural fixture reports for model
  and domain adapters;
- `lab` supplies zero-state genesis, predeclared runs, immutable terminal
  seals, complete evidence capsules, and non-adopting promotion receipts;
- `shorthand` supplies the experimental Kevin Speak representation ledger,
  exact residual fallback, and sealed successor recommendation/adoption path;
- `scribe` supplies a separately identified representation-only provider and
  coordinator. Its request view contains receipt-bound derived adaptation
  summaries and omits held-out validation payloads, actions, domain state,
  private reasoning, and authority. The in-process callable adapter is trusted
  application code, not a confidentiality sandbox;
- `pal23` supplies a targeted prospective adapter for role-typed boundaries,
  explicit work projections, immutable grant epochs, checkpoint freeze/thaw,
  heartbeat stutter, and re-entry. It is not a package-wide conformance claim;
- `curriculum` supplies sequential bounded campaigns with explicit learned-state
  transfers, while `heartbeat` supplies event-driven steering projections;
- `features` keeps experimental capabilities replaceable and inert by default;
- `provenance` validates the exact paper and policy source registry.

## Separation invariants

Strongwiz preserves these distinctions across every adapter:

- observation is not interpretation;
- a candidate rule is not an accepted rule;
- description is not recommendation, permission, or authorization;
- model output is not control state;
- a routed proposal is not an executed action;
- a prediction match is support, not proof;
- a terminal-looking state is not success unless the domain authority says so;
- a closed attentional surface remains reopenable when new evidence makes it
  relevant again.

Every session also binds one validated `FrozenRuntimeManifest` object. When a
ledger is present, that manifest is stored in the content-addressed object
store and referenced by every session receipt. This establishes declared
runtime identity. The exact model-driver object, its version/artifact, the
domain version/artifact, and active router/cadence digests must match that
manifest at initialization and again at their call boundaries. This revalidates
declared identity; it is not a hostile-code sandbox or proof of loaded-code
identity. File-level verification remains a separate explicit check.

Session transitions use persist-before-advance ordering when a ledger is
configured. A failed durable append cannot leave the session able to act on an
unreceipted scan or decision. Assessment additionally requires the matching
completed release, execution-attempt receipt, executor evidence, and the exact
decision route/control pair.

`SessionCheckpoint` v1 extends the concise `SessionReceipt` with exactly the
active request, pending proposal, repeated-failure guard, account/version, and
history needed to restart any phase. Restoration revalidates the frozen
runtime, driver, domain, policies, account, exact latest ledger boundary, and
parent chain, then continues without repeating a model call or environment
action. The checkpoint is a distinct `strongwiz.session-checkpoint.v1` wire
schema; consumers needing `strongwiz.session-receipt.v1` must use the explicit
`concise_receipt()` projection. A checkpoint with any durable receipt lineage
can only be restored with its original ledger; only a genuinely ledgerless
checkpoint may be transported without one.

New durable writes use `strongwiz.session-checkpoint.v2`. V2 stores typed
history counts, the exact predecessor receipt, and only current actionable
objects by reference. Restore reconstructs the full v1-compatible in-memory
state from the verified original ledger. This removes repeated growing history
arrays from checkpoint payloads without weakening restart identity. Historical
v1 checkpoints remain readable.

Lab verification and run sealing now validate ledger rows incrementally and use
a disk-backed identity index for uniqueness and reference closure. Their
canonical-array projection hashes remain byte-identical to the v1 contract, and
memory grows with the largest row rather than total row count. Evidence-capsule
packing still uses the audited materialized exporter; a streaming packer has not
yet been promoted into the kernel.

The reusable Strongwiz source/runtime and an experiment lab are separate
objects. A lab begins with a recorded empty ledger and no domain state. A later
run may promote only a bounded candidate mechanism through a separate receipt;
it cannot silently inherit action sequences, learned domain state, replay
state, hidden reasoning, or authority.

Provider packages are drivers, domain packages are adapters, and Hearthline or
another configured product may compose them as a distribution. None of those
identities belongs in the kernel contract.

The v3 calibration design strengthens this boundary further: every comparison
arm has a physically separate absent-or-empty lab root and SQLite ledger. A
campaign index outside those roots may bind only plans, identities, checkpoints,
and seals. Same-game carry evidence crosses only through a reviewed,
target-bound packet and changes the claim class to adaptive successor.

The scribe shares the run's serial evidence writer but uses its own account and
driver identity. Its only productive coordinate is the representation state.
An earned codebook promotion is not an environment action or evidence of task
progress. Validation material is omitted from the declared request view until
the proposal draft is frozen; provider failure preserves the pending entries
and their exact decodability in whichever compact-or-residual lanes ingestion
already selected. A separate-process capability boundary is required if the
provider itself is not trusted application code.
