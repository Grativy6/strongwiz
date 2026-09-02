# PAL v2.3 profile

Status: **targeted prospective adapter for the Strongwiz v3 campaign**.

Strongwiz does not claim full PAL v2.3 conformance. This profile adopts only
the mechanically relevant boundary, transport, checkpoint, cadence, and grant
distinctions needed to make the next experiment auditable. Calibration 001 and
Calibration 002 remain immutable historical evidence under their original
contracts; this is a `SUCCESSOR`, not a retrofit.

## Controlling sources

The five PAL v2.3 components are registered by exact file hash in
[`source-identities.json`](source-identities.json). Their roles remain
distinct:

- the Mechanical Structural Spine states the structural rules;
- the Compatibility Note controls version relationships and migration claims;
- the Obligation and Decision Ledger preserves decisions and open burdens;
- the Mathematical Realization Atlas supplies optional mathematical
  realizations and tests but does not contain or redefine PAL; and
- the Conformance Tests define the declared test surface but do not amend the
  other components when a test passes.

All five are Christopher D. Pang's 2026-09-02 CC BY 4.0 author release under
DOI `10.5281/zenodo.22240134`.

## Adopted v3 surface

The executable profile in `strongwiz.pal23` carries these bounded additions:

1. A `BoundaryRef` names one role-typed boundary: cut, chain, interface,
   topological boundary, constraint boundary, scope boundary, or transport
   validity boundary. Reusing a word does not coerce one role into another.
2. A `BoundaryAdapter` must bind source and target roles, hypotheses, preserved
   data, lost data, evidence, authority ceiling, and reopening condition.
3. A `StateProjection` names the exact state space, included and excluded
   coordinates, comparator, and provenance used by any equality, return,
   no-change, or stutter claim.
4. A `GrantEpoch` calculates slack only inside its immutable epoch. A top-up
   creates a successor epoch; it cannot rewrite spent resources or renew
   authority by implication.
5. A `CadenceTransition` separates an administrative transition from a
   productive transition. A heartbeat may advance audit state while preserving
   the declared work projection; it is not progress.
6. A `CheckpointCapsule` binds the declared work state, cursor, comparator,
   schedule, code, dependencies, environment, invariants, grant epoch,
   resources, authority ceiling, audit state, residuals, trace anchor, and
   external-effect boundary.
7. A `CheckpointThawReceipt` embeds and binds the complete capsule, identifies
   the work projection being compared, and requires one evidence-bound
   `CoordinateRevalidation` for every declared non-work coordinate. It can
   establish equality only on that work projection. It cannot restore spent
   resources, expired grants, or authority. A material mismatch becomes a
   transport break and requires re-entry.
8. A `TransportReceipt` supplies common fields for boundary-adapter,
   checkpoint-freeze, checkpoint-thaw, heartbeat-stutter, and re-entry
   profiles. Checkpoint profiles embed the capsule, bind its digest, and bind
   the work projection plus source/target work state to that capsule rather
   than accepting a digest-shaped label alone. None transfers authority.

These contracts target the concerns exercised by PAL v2.3 tests T49, T60, and
T62-T67. Passing Strongwiz's tests establishes only the tested software
behavior, not conformance of the whole Strongwiz repository or adoption of all
PAL v2.3 clauses.

## Native v3 mappings

| Strongwiz event | PAL v2.3 profile | Required claim boundary |
|---|---|---|
| Concise evidence enters the scribe | `BOUNDARY_ADAPTER` | Declared derived projection only |
| Scribe or operator checkpoint | `CHECKPOINT_FREEZE` | Exact capsule fields, no implied return |
| Restart | `CHECKPOINT_THAW` | Revalidate every bound non-work coordinate; carry equality only on the named work projection |
| Unchanged human-facing update | `HEARTBEAT_STUTTER` | Audit motion only; no progress or liveness claim |
| Changed dependency or projection | `REENTRY` | Preserve break, reopen locally, do not silently resume |
| Resource top-up | successor grant epoch | New budget epoch; prior consumption remains spent |

The scribe's promotion of a shorthand definition is a productive
representation transition only. It is not evidence of better reasoning or
game progress. Environment progress remains a separate domain-owned
coordinate, and ARC completion remains authoritative only when the official
environment reports `GameState.WIN`.

## Explicit nonclaims and burdens

- The adapter does not implement every PAL v2.3 decision, obligation, profile,
  or conformance test.
- The source registry proves file identity, not correctness or independent
  corroboration.
- A checkpoint proves no more continuity than the named projection.
- A heartbeat establishes neither progress, success, liveness, consciousness,
  permission, nor authorization.
- Mathematical realizations remain optional tools. They do not create
  authority or close an empirical burden.
- Any later change to the projection, decoder, environment, dependency set,
  grant, resource state, or authority surface reopens the implicated transport
  claim.

Print the exact executable schemas with:

```console
strongwiz pal23 schema
```
