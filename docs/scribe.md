# Representation scribe

The Strongwiz scribe is a dedicated, replaceable representation worker between
concise model-produced summaries and the Kevin Speak ledger. Its purpose is to
notice recurring structures that the action-selecting model may not have spare
attention to encode. It is not a second planner, an executor, or an authority.

Status: **experimental and off the environment-action path**.

## Role boundary

The action model observes, predicts, selects probes, and proposes actions. The
scribe receives only a closed class of receipt-bound, derived working material:

- decision summaries;
- outcome summaries;
- residual summaries;
- mechanic summaries; and
- checkpoint summaries.

The scribe receives no action port, environment writer, control state, task
grant, permission, authorization, raw frame, raw trace, domain state, action
sequence, or private reasoning. Its output is a declarative `ScribeDraft` made
of proposed Kevin Speak symbols and explicit residuals. Strongwiz—not the
scribe—owns storage, evaluation, promotion, and the active codebook.

This is a typed transport boundary, not a semantic content oracle. The closed
`ScribeEvidenceAtom` schema makes declared raw/private/action/authority fields
unrepresentable, and every evidence reference must resolve in the supplied
ledger. The upstream producer still owns the truth and completeness of its
summary and the honesty of its classification. Strongwiz cannot prove that
free text does not paraphrase material that should have remained outside the
boundary; deployments must enforce that rule where the summary is produced.

```text
action model -> typed concise summaries -> ScribeSession
                                        -> adaptation-only view -> scribe driver
                                        <- advisory symbol proposals
                held-out summaries ----> fixed Kevin evaluator
                                        -> earned promotion or residual fallback
```

The scribe and action model bind separate driver identities and artifacts.
Replacing either one does not rewrite the identity of the other.

## Mechanical lifecycle

1. `ScribeSession.open` records a representation-only genesis, frozen policy,
   PAL v2.3 boundary adapter, exact work projection, and scribe-driver binding.
2. `ingest` accepts one source-bound derived summary. Kevin Speak stores it in
   the compact or residual lane and verifies exact reconstruction.
3. `should_run` responds only to material events. Timer-only triggering is
   forbidden.
4. `run_cycle` makes a deterministic adaptation/validation split. The declared
   request view contains adaptation payloads only; validation payloads are not
   delivered through that interface before the draft is frozen.
5. Every proposal must cite adaptation sources containing its exact expansion.
   Unknown or held-out citations fail closed.
6. Existing Kevin gates charge the candidate codebook, demand exact round trips,
   demand disjoint validation improvement, and demand net representation-byte
   savings before a promotion can be earned.
7. A provider exception produces a typed failed-cycle receipt. The source
   material stays pending and losslessly decodable.
8. The request is journaled before a provider call and the returned draft is
   frozen before Kevin-state mutation. A crash with an unresolved provider call
   or partial cross-account mutation stops for explicit re-entry; it is never
   answered by silently calling the provider again.
9. `restore` reconstructs and cross-validates scribe and Kevin receipts.
   Reusing a semantic cycle ID returns the same receipt only when the trigger,
   policy, driver, and material frontier are identical. Changed semantics
   require a new ID; forged or incomplete promotion lineage fails closed.
   Kevin entry and evaluation identities use injective session-bound encodings,
   so separate scribes may share one Kevin workspace without collapsing equal
   human-readable cycle or material labels.

No-candidate, deferred, not-earned, and failed cycles are valid experimental
outcomes. They may be more informative than a forced codebook.

## First experiment: decoded storage

The v3 experiment keeps `decoded_storage` as the default. A promoted shorthand
may reduce durable representation size, but the action model continues to
receive decoded material. This isolates representation mechanics from changes
to the model's prompt language.

Only a later, separately preregistered ablation may use `model_facing` shorthand.
That requires evidence that the model can orient and reason with the notation
without losing consequential distinctions.

## Full cost and benefit ledger

A promoted codebook is not automatically useful. The campaign must report:

- source, compact, residual, codebook, request, response, evaluation, review,
  adoption, transfer, and verification bytes;
- scribe calls, failures, retries, latency, context, compute, and memory
  high-water;
- exact round trips and decode failures;
- proposals, evaluated definitions, promotions, retirements, and reopens;
- operator reorientation work and action latency; and
- domain progress, actions, resets, calls, and wall time under matched budgets.

Representation savings establish only representation savings. A reasoning or
play benefit requires a matched comparison on the corresponding outcome.

## Heartbeat relationship

The heartbeat is a human-facing projection of durable state, not a second
ledger. In v3 it should emit only for a material boundary such as evidence
ingestion, a frozen scribe draft, evaluation, promotion, failure, checkpoint,
re-entry, or closure. Repeated timer pings with unchanged work are suppressed.

Under the PAL v2.3 profile, an unchanged heartbeat is an administrative
stutter: the work projection and progress coordinate remain equal even if an
audit cursor advances. A codebook promotion is productive only on the
representation coordinate. Neither event can manufacture environment progress
or authority.

## Current claim ceiling

The implementation and synthetic tests can establish request/driver binding,
held-out omission from the declared request view, exact decoding, mechanical
promotion gates, failure preservation, and restart/idempotency on the tested
software surface. `CallableScribeDriver` is trusted in-process application
code, not a confidentiality sandbox; an untrusted provider requires a later
separate-process capability boundary. These tests do not establish that:

- the provider lacked access through a captured or separately supplied object;
- the scribe understands the source material;
- the summaries are true or complete;
- compression preserves a model's behavior;
- shorthand improves reasoning, speed, or ARC play; or
- the scribe can act, authorize action, or confer permission.

Print the declared boundary with:

```console
strongwiz scribe schema
```
