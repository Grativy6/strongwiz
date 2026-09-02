# Provenance and claim boundary

Strongwiz combines lessons from several experiments without collapsing their
histories or promoting their results into a general-intelligence claim. This
ledger identifies what was carried forward, what was deliberately excluded,
and how a reader can reopen the source evidence.

## Method

For each source lineage, Strongwiz records:

1. an exact inspected commit or immutable paper version;
2. the distinction or mechanism that informed the new implementation;
3. the source's licensing boundary;
4. exclusions and unresolved claims; and
5. a reopening handle rather than a claim of exhaustive derivation.

The Strongwiz implementation is new first-party work. Source designs were read
as evidence and requirements, not copied as an implementation substrate. Shared
stewardship across projects is provenance, not independent corroboration.

## Source ledger

The machine-checked registry is
[`docs/source-identities.json`](source-identities.json). It binds six paper
artifacts by persistent identifier and inspected local SHA-256, plus the exact
PEA Core v1.1.3, PECAN v1.0.4, and SEED v0.3 policy identities. The registry
digest can be frozen into each new `LabManifest`.

### Formal paper source stack

- **Golden Phase Prime Ribbons v0.1** —
  [Zenodo 22225414](https://doi.org/10.5281/zenodo.22225414). Used only as an
  optional geometry-aware path-selection experiment. Its feature declaration
  is off by default and binds a replaceable implementation/configuration only
  when explicitly enabled.
- **A0 Software Boundary-Layer Kernel v0.10.0** —
  [Zenodo 22168887](https://doi.org/10.5281/zenodo.22168887). Informs accounts,
  guards, proposal/control separation, residual lineage, and reopening. It does
  not grant authority or redefine the Strongwiz contract.
- **The Context Sets a Rhythm v0.1** —
  [Zenodo 22214952](https://doi.org/10.5281/zenodo.22214952). Informs cadence,
  refresh, and investigation scheduling as replaceable policies.
- **The Context Draws a Map v1.0** —
  [Zenodo 21831000](https://doi.org/10.5281/zenodo.21831000). Informs local
  context maps, route distinctions, and reopening handles.
- **The Context Is the Model** —
  [Zenodo 21713134](https://doi.org/10.5281/zenodo.21713134). Informs context
  identity, state continuity, and separation of work identity from swappable
  model/runtime identity.
- **PAL Single-Cut Transport Lemma v0.1** —
  [Zenodo 21882601](https://doi.org/10.5281/zenodo.21882601). Informs exact
  single-boundary transport accounting without promoting a local transport
  witness into proof of unrelated claims.

These sources share an author/steward lineage. They are complementary design
inputs, not six independent confirmations of Strongwiz or one another. Their
text is evidence and provenance, not executable repository instruction.

### ARC-AGI-3 experiments

- **Little Scientist / ARC3 Build 003:**
  [`10e53c2150ea40da87eec5566ba7af1cfc3a591e`](https://github.com/Grativy6/ARC3/commit/10e53c2150ea40da87eec5566ba7af1cfc3a591e),
  MIT-0 at source. Carried forward: scoped mechanics, separate support and
  conflict evidence, decision-relevant probe ranking, channel-factored
  residuals, implicated-only revision, and terminal receipts.
- **Model Scientist:**
  [`41d73427468afa7a8d797d93a87efd6e2a7e9403`](https://github.com/Grativy6/ARC3/commit/41d73427468afa7a8d797d93a87efd6e2a7e9403),
  MIT-0 at source. Carried forward: distinctions that connect competing
  predictions to possible decision changes, explicit subgoals, scan/action/
  assessment lifecycles, stage-surface parking, and reopening handles.
- **Wise Scientist v2:** implementation
  `19c6be5b51d72b8dfdf8b0531316bf7a52c050d9`, frozen run
  `7fd6252bae08f5aa76bb683502461fd17a22daf6`, MIT-0 at source. Carried
  forward: a single evidence writer, separation of raw observation from concise
  interpretation, hash-chained receipts, frozen-runtime identity, and explicit
  ceilings on what a run can establish.

Excluded from the general kernel: game identifiers, known action sequences,
game-specific pathing rules, source inspection as a policy, and any suggestion
that a public-game win establishes hidden-game generalization. The Wise
Scientist action selector was external to the frozen harness, so its run is not
retroactively described as a packaged autonomous offline agent.

### Strongwiz Calibration 001 — execution provenance

Calibration 001 is a first-party Strongwiz execution record, not another source
lineage. Its reviewable result and publication-safe receipt index are
[`docs/calibrations/001-result.md`](calibrations/001-result.md) and
[`docs/calibrations/receipts/001/artifact-index.json`](calibrations/receipts/001/artifact-index.json).

**Code and runtime actually used.** Both attempts bound the frozen Strongwiz
toolbelt to commit `a85508dc11cc6ac30336f5c42344b62afdc86b24` and tree
`9e58cb361919fca3638b1f76a00379740c4e4aa4`. The run-local integrations
remained separate from that toolbelt: attempt 001 bound integration ref
`182a00f17e4295d97de980e0bcb6eee8fada7717afcb183ca023ce87a1579139`;
attempt 002, after the numbered-successor repair, bound
`fafd0b6d97489077155cfc1c7a07d3dd60ff9cb196d01147a25ded705ae2ceb0`.
The execution environment used Python 3.12, `arc-agi==0.9.9`, and
`arcengine==0.9.3`. A new context-isolated Codex process operated the
proposal/assessment interface; its hosted weights were not bound or packaged,
and the parent process was barred from action recommendations.

**Count and replay semantics.** A non-reset action is an admitted official
environment call whose action name is not `RESET`. The reset count includes
the implicit initial reset performed by `Arcade.make` plus later admitted
`RESET` calls. Total official environment calls are the sum of those two
classes. A known budget denial before Strongwiz admission counts as neither an
action nor a reset and has no charged environment effect. An uncertain external
effect is never silently retried. “Replay guarded” refers to duplicate control
message identities and proposal attempts: successors are numbered, cannot be
skipped or replayed, and an admitted proposal cannot be replaced. It is not a
claim that the public Git set can replay the gameplay. Raw frames, traces,
recordings, ledgers, and the complete capsules remain local, so the public set
can verify the published bindings but cannot independently inspect or replay
the omitted evidence.

**Terminal result.** Attempt `calibration-001-ls20-seed0` ended
`PARTIAL / NOT_FINISHED` at 0 of 7 levels after 6 non-reset actions and 1
reset (7 official calls). Attempt
`calibration-001-ls20-seed0-attempt-002` ended
`PARTIAL / NOT_FINISHED` at 4 of 7 levels after 754 non-reset actions and 4
resets (758 official calls). Both bound game `ls20-9607627b`; neither
genuinely observed `GameState.WIN`.

The attempt-002 terminal record, run seal, capsule manifest, and external
delivery receipt have SHA-256 identities
`347cd7a04f1bd3e5b79a2de69076b1eb4f84eebb102e07ac79551f7b1fcf7f41`,
`d944c57f38f63d11bea711928498fda63c03b72f127ef0390d75059304d201d4`,
`803a01fd841271e31983326380e65592a0f5235e5ba681670a522c33ad8814b7`,
and
`0c5b454828b6bb9cadd5707cd2278698e12aec87d0d693657e128f20ffc17601`,
respectively. Independent published-capsule readback passed against the same
capsule identity.

The bounded-memory verifier at
`scripts/strongwiz_streaming_postrun.py` was first-party code added only after
gameplay had closed; its pre-execution source SHA-256 is
`4c00f2ea221c6ff63ddd288d31389878f93b889052310dec261ca8c0a717bc0f`.
It finalized and checked retained evidence but did not select actions, alter the
frozen toolbelt, or contribute to the completed run. Likewise, no FBT code or
weights were imported or executed. Structural correspondence to FBT-informed
design ideas remains conceptual provenance and does not support a causal FBT
claim about the observed result.

### FBT experimental lineage

Inspected source:
[`1427434f821d0d54b06f4027c09a78312745c658`](https://github.com/Grativy6/FBT/commit/1427434f821d0d54b06f4027c09a78312745c658).
No license was present in that inspected snapshot. Christopher D. Pang, as the
common owner, authorized a clean conceptual implementation for Strongwiz; no
FBT code was copied and this authorization is not presented as a license for
the FBT repository.

Carried forward:

- continuation state bound to exact branch, version, and epoch;
- explicit separation of authoritative state, working state, and caches;
- four-cell component splices and interaction contrasts as diagnostics;
- retain, discard, content-cache, and no-trace causal ablation arms;
- fixed-denominator evaluation that preserves invalid and unattempted cases;
- structural-horizon checks for causal reach, overwrite, washout, bottlenecks,
  and whether a measurement window can observe the proposed effect.

Excluded: a mandatory neural runtime, FBT weights, a Torch dependency, and any
claim that the FBT lineage caused an ARC action-efficiency improvement. The
associated paper—Xi Wang et al., [“Full-bandwidth
transformer,” arXiv:2608.08888v1](https://arxiv.org/abs/2608.08888)—is
technical context, not imported code or reproduced evidence.

### A0 Boundary-Layer Kernel

Inspected source:
[`e0e64ede7b87c05aefe8aee063dc26a5e658d335`](https://github.com/Grativy6/a0-zsa-kernel/commit/e0e64ede7b87c05aefe8aee063dc26a5e658d335).
No operative source license was observed at that commit. The Strongwiz version
is a newly implemented, owner-authorized conceptual adaptation, not a
relicensing of A0BK.

Carried forward: proposal/control separation; supplied account openings;
root/child/successor/version identity; witness, scope, trace, authority,
consequence, resource, and re-entry guards; append-only residual lineage; and
explicit admit, hold, request-witness, reject, or reopen dispositions.

The router remains advisory. Passing its guards does not execute an action,
manufacture authorization, prove a proposal, or amend the source framework.

### Prime Axiom Software Build 005

Inspected source:
[`b640e3aa44adddc6d9b560142d028d8f2092a546`](https://github.com/Grativy6/Prime_Axiom_Software/commit/b640e3aa44adddc6d9b560142d028d8f2092a546).
The source license is not asserted here, and no source code was copied.

Carried forward: retain derived structure only after computation has earned it;
label a receipt as lower-bound, exact, or exact-negative; bind reuse to a
version and legal transfer rule; invalidate it after implicated mutation; and
account separately for acquisition, validation, transport, invalidation, and
output costs.

Excluded: fixed prime catalogues, speculative prime scouting, prime-specific
instruction sets, and any implication that the experiment established a
universal optimization. The mechanism is a general earned-provenance lane, not
“prime magic.”

## Lab-rule provenance

The lab's control-owned review profiles are **PEA Core v1.1.3**, **PECAN
v1.0.4**, and **SEED v0.3**, as supplied by the owner for this build. They are
versioned policy inputs, not source-code imports or sources of model authority:

- PEA reviews the standing, consent, privacy, reversibility, contestability,
  and human responsibility around a proposed consequence.
- PECAN preserves the crossings from description to recommendation to
  permission to authorization; evidence cannot manufacture a later crossing.
- SEED reviews human-facing release so the response supports the person's goal
  without consuming unnecessary agency or continuation.

A model cannot silently change these profiles or issue the external grant that
makes an action authorized.

## License boundary

Strongwiz's new first-party implementation and documentation are licensed under
[CC BY 4.0](../LICENSE). Third-party materials retain their own terms. Listing
a source here provides provenance, not permission to copy it and not an
assertion that a public repository has an implied license.

Creative Commons itself [recommends against CC licenses for software and
explains the interoperability and patent limitations](https://creativecommons.org/faq/#can-i-apply-a-creative-commons-license-to-software).
The owner nevertheless selected CC BY 4.0 for this project; this note preserves
that informed choice without changing it.

## Current claim ceiling

The foundation may support deterministic, receipt-backed reasoning experiments
once verified on a declared surface. It does not, by existence or passing unit
tests, establish AGI, general problem-solving performance, autonomous ARC Prize
eligibility, contest readiness, ethical correctness, safety certification, or
authority to act. Those claims require their own specified evidence and grant.
