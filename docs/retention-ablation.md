# Retention ablations

Strongwiz treats retained structure as an earned optimization, not a reason to
search speculatively for reusable structure. A derived fact belongs in the
sidecar only after a computation or observation has produced it, named its
scope and version, and paid its acquisition and validation cost.

The generic retention experiment compares three logically isolated runner
instances on the same preregistered workload:

1. `DISCARD`: recompute without retained state;
2. `CONTENT_CACHE`: reuse a generic content-addressed result;
3. `EARNED_RECEIPT`: reuse an eligible, version-bound derived-fact receipt.

Each arm retains the original fixed denominator after invalidity or early
termination. Unattempted steps remain explicit. Costs stay componentwise:
acquisition, validation, transport, invalidation, output, computation, memory,
time, and environment interaction are not collapsed into a universal scalar.

`run_retention_ablation` now requires two caller-supplied maps in addition to
the runner factory:

- `attempt_occurrence_ids: Mapping[AblationArm, str]`; and
- `isolation_evidence_refs: Mapping[AblationArm, str]`.

Both maps must bind exactly the three registered arms. Occurrence identities
and isolation evidence references must each be unique across arms. The trace-use
API requires the corresponding `Mapping[TraceArm, str]` values for all six trace
arms. Aggregate validators also require exactly one result per enum member; set
membership cannot conceal a duplicated arm.

Attempt-start receipts bind the scenario, frozen runtime, seed, budget, retry
policy, caller-supplied occurrence identity, caller-supplied isolation evidence,
and denominator before an outcome exists. Frozen-runtime references, isolation
references, step receipt references, and terminal evidence references must be
lowercase 64-character SHA-256 hex digests.

Every arm in one aggregate must carry the identical preregistered scenario,
frozen runtime, denominator, seed, componentwise budget, retry policy, and
isolation-evidence status. Its experiment, arm identity, and role must also
match the aggregate and registered enum member. These checks prevent a complete
arm from another schedule or runtime from being spliced into a comparison.

Every returned arm contains both its `AttemptStartReceipt` and a linked
`AttemptTerminalReceipt`. The terminal receipt points to the start digest,
repeats the exact disposition, denominator counts, costs, and failure category,
and retains the supplied isolation evidence reference. It must account for
every registered step. Retry eligibility is restricted to a preregistered
infrastructure-only policy; a disappointing mechanism result cannot manufacture
another attempt.

## Occurrence and isolation boundary

Strongwiz keeps a thread-safe, in-process registry keyed by
`(experiment_id, arm_id, attempt_occurrence_id)`. Reusing the same occurrence
key in one Python process is refused before another set of runners starts. A new
attempt must receive a new caller-supplied occurrence identity.

This registry is an accidental-replay guard, not durable or distributed
coordination. It resets with the process and does not authenticate a machine,
worker, container, or human operator. Cross-process orchestration must enforce
global uniqueness in its own authoritative store.

Likewise, each `isolation_evidence_ref` is explicitly recorded with status
`caller_supplied_not_authenticated_process_isolation`. Unique hashes prove only
that the caller supplied distinct reference values. Strongwiz does not inspect
the referenced artifact here and does not claim that separate processes,
containers, hardware, memory, caches, or randomness were actually used.

The core runner rejects reuse of the same runner object across arms. It does not
itself create operating-system processes, containers, or independent hardware
environments. Integrations needing process isolation must provide it inside
each runner factory, bind that environment in the frozen runtime, and supply an
evidence artifact that another verifier can inspect. Likewise, the matched
schedule and oracle fields are declared evidence bindings; an integration must
execute and receipt their independence when that claim matters.

Consequently, this harness has a closed `ComparisonStatus` taxonomy:

- `mechanically_incomplete` means at least one registered arm did not complete;
  and
- `mechanically_complete_causal_not_established` means every fixed-denominator
  arm completed, but the supplied isolation evidence remains unauthenticated.

The compatibility field `comparable` is always `false` under this boundary. A
caller cannot turn distinct hashes, distinct function wrappers, or successful
steps into a causal or scientific comparison claim. In particular, separate
runner callables can still close over the same mutable state; mechanical
completion records that execution fact without authenticating isolation.

## Budget and failure taxonomy

Every accumulated `CostVector` is compared component by component with the
preregistered arm budget. No dimension can borrow slack from another. On the
first returned step whose cumulative cost exceeds any component, Strongwiz:

1. retains that step and its incurred cost;
2. marks it invalid with `component_budget_exceeded:<dimensions>`;
3. stops accepting later steps and pads the denominator as unattempted; and
4. returns `FAILED_ASSERTION`, making the experiment mechanically incomplete.

The harness cannot undo work that a batch runner performed before returning
its sequence. A runner controlling consequential or expensive actions must
also enforce the same budget before each action; the outer check is an evidence
and comparability guard, not authenticated resource containment.

Failure kinds remain distinct:

- an invalid attempted step or explicit `RunnerMechanismFailure` is
  `FAILED_MECHANISM`;
- runner-contract violations, reused runner instances, component budget
  overruns, and unexpected Python exceptions are `FAILED_ASSERTION`; and
- only an explicit `RunnerInfrastructureFailure` is
  `FAILED_INFRASTRUCTURE`.

This taxonomy prevents an ordinary exception or failed assertion from being
reported as independent infrastructure evidence. All three terminal failures
retain the fixed denominator and their category.

## Claim ceiling

A lower measured cost in one mechanically matched workload describes only that
registered evidence. Under caller-supplied unauthenticated isolation it does not
by itself establish that the tested mechanism caused the difference, a
universal cache benefit, a general reasoning improvement, or a reason to scout
for special structure in advance. Exact-negative facts may prevent repeated
failed acquisition, but mutation or version drift invalidates their reuse just
like a positive fact.

The Prime Build 005 result motivating this module found generic caching to
explain most observed reuse and did not support blind prime scouting on its
registered workloads. Strongwiz therefore preserves provenance when work has
already earned it and leaves speculative acquisition to an explicit experiment.
See [provenance](provenance.md) for the source boundary.

## Trace-use falsification

Presence is not causal use. `run_trace_use_ablation` therefore supports six
matched arms: normal trace, swapped trace, duplicated left trace, duplicated
right trace, zeroed trace, and no trace. The design binds identical visible
inputs to at least two conflicting targets, records a heldout-first
construction, and binds one matched schedule and independent oracle before
execution. Strongwiz hashes those five outcome-independent fields into a
`design_ref` before any runner is created. Every attempt start, terminal receipt,
trace-arm result, and aggregate result binds that digest; aggregate validation
reconstructs it from the visible-input, conflicting-target, schedule, oracle,
and heldout-first references.

The in-process harness can therefore establish mechanical perturbation evidence
for that exact design. It cannot establish causal trace use until an external
integration supplies and authenticates the required isolation and oracle
execution evidence. No result here implies general memory, understanding, or
agency.
