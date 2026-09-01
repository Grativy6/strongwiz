# Adapters and plugins

Strongwiz keeps providers and domains replaceable behind small protocols. An
adapter should translate at the edge and leave the versioned core contracts
unchanged.

## Model drivers

A `ModelDriver` has a stable ID, version, and artifact reference and accepts a
`ReasoningRequest`. A session pins the exact registered object at construction;
later calls cannot substitute another object that reuses its string ID. It
returns candidate proposals only. Each proposal must bind the current
observation and goal, a meaningful distinction, a falsifiable prediction,
evidence references, alternatives, action parameters, heterogeneous costs,
and a concise rationale.

A driver must not supply its own `ControlSnapshot`, execution grant, terminal
status, or action receipt. Hosted models, local models, symbolic systems, and
human-in-the-loop proposers can use the same interface. Hidden chain-of-thought
is neither requested nor part of the contract.

`CallableModelDriver` is the smallest local integration: the provider returns
`ProposalDraft` values and Strongwiz supplies exact request bindings.
`FramedModelDriver` carries those same drafts over bounded, length-prefixed,
canonical binary JSON. It validates checksums, strict UTF-8, exact reply
identity, partial I/O, timeouts, size limits, and replay within a declared
window. It does not use a TTY or newline-delimited protocol and makes no network
call. Framed reconstruction requires a caller-retained
`FramedModelRestartState`; crash durability additionally requires the caller's
state sink to persist each reservation before returning. The adapter does not
supervise or restart a provider process.

## Domain adapters

A `DomainAdapter` has an ID, version, and artifact reference. It normalizes raw observations, declares available actions,
extracts an outcome after execution, and supplies the domain's terminal
authority. It should preserve a content-addressed reference to raw input and
keep domain-specific payloads out of the kernel.

The included ARC-AGI-3 module is deliberately narrow: it defines terminal-state
semantics and a run-receipt shape. It contains no SDK, game policy, game ID
solution, or action script.

## Capabilities

A `ReasoningCapability` contributes evidence references to a request without
taking over routing or execution. Examples include perception, path search,
mechanic retrieval, consequence factoring, causal splicing, and experiment
design. Capabilities should declare producer identity and version wherever
their outputs may be retained.

## Execution

An `ActionExecutor` is an external single writer pinned to one coordinator. It
receives an evidence-bound, non-authorizing `ExecutionCommand` containing the
exact action, invocation, admission, and idempotency identities; a bare permit
token is never passed into adapter code. The executor returns raw post-action input
paired with an immutable evidence reference. The grant registry independently
limits the task, goal pair, scope, action aperture, executor, destination,
lifetime, invocation budget, replacement, and revocation. Routing remains
advisory even when every guard passes.

The coordinator consumes the one-use permit immediately before calling the
bound executor and returns separate grant-release and execution-attempt
receipts in an immutable, nonserializable coordinator-issued handoff. Any
exception after the call begins is `UNKNOWN_EFFECT`, because an
external consequence may already have happened. It is never relabeled as an
ordinary infrastructure failure or successful action. A reasoning session
accepts assessment only from that handoff's exact completed release, attempt,
decision route/control pair, proposal, action, executor identity/artifact, and
executor-evidence tuple.

This is in-process one-use control, not a distributed transaction. A process or
device crash during an irreversible external call can still leave an unknown
effect; production executors receive an idempotency key but still need their own durable intent/
completion journal, and recovery protocol for that boundary.

Python is not a hostile-code security boundary. An integration that retains a
direct reference to its executor can call that object outside Strongwiz. The
one-use and lab-rule guarantees apply to the supported coordinator path;
least-privilege process isolation is required when host code is untrusted.

## Registration and packaging

`DriverRegistry` registers model, domain, and capability identities and rejects
conflicting duplicate registrations. Provider packages may expose registration
functions or Python entry points around this registry; they should not fork the
contract types.

For a reproducible run, freeze the exact source files, package version,
dependency lock, configuration, model driver and artifact, domain adapter and
artifact, capabilities, exact router/cadence policy digests, and runtime
description in a `FrozenRuntimeManifest`.
The session rechecks the model, domain, and policy declarations at their call
boundaries. The manifest makes the action-selecting boundary inspectable. It
does not prove that a driver reports its identity honestly, or that the frozen
system is correct or generally capable.

Compatibility should be tested at two levels:

- contract tests: schema, immutability, canonical serialization, stale-state
  rejection, and control separation;
- behavioral tests: deterministic fixtures, budget ceilings, malformed driver
  output, grant replacement, terminal handling, and receipt replay.

The `conformance` module provides one bounded structural fixture for model and
domain adapters. A pass is useful admission evidence for a lab manifest, but it
does not establish model quality, repeatability beyond the fixture, domain
completeness, executor safety, or authorization.
