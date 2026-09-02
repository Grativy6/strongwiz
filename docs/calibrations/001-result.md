# Calibration 001 — Result

Status: **PARTIAL; official `GameState.WIN` was not observed.**

This report uses the evidence and post-run inputs enumerated at its end. Raw
frames, PNGs, raw trace contents, official recording contents, game source, and
capabilities or tokens were not inspected for this report.

## Frozen scope and claim ceiling

Calibration 001 tested the frozen Strongwiz toolbelt at commit
`a85508dc11cc6ac30336f5c42344b62afdc86b24` (tree
`9e58cb361919fca3638b1f76a00379740c4e4aa4`) with Python 3.12,
`arc-agi==0.9.9`, `arcengine==0.9.3`, seed `0`, and the preregistered public
game `ls20`. Both terminal records bind the exact game ID
`ls20-9607627b`. The evaluation class and claim class remain
`local-public` and `local-public-codex-operated-strongwiz-calibration`.

The preregistered success statement required the official environment itself
to report `GameState.WIN`. Neither attempt did so. The clean result is therefore
limited to the following:

> Two context-isolated, Codex-operated Strongwiz attempts on the preregistered
> local-public game `ls20-9607627b` ended `PARTIAL` and `NOT_FINISHED`. Attempt
> 001 completed 0 of 7 levels before an integration deadlock; attempt 002
> completed 4 of 7 levels before the wall-clock boundary. No official `WIN` was
> earned or genuinely observed.

This is not a competition entry or Kaggle result, a private or official
evaluation score, an autonomous-offline result, or a generalization claim. It
does not establish AGI, consciousness, PAL, or a general theory. Reaching 4 of
7 levels is bounded progress evidence, not completion.

## Exact attempt results

| Field | Attempt 001 | Attempt 002 |
|---|---:|---:|
| Run ID | `calibration-001-ls20-seed0` | `calibration-001-ls20-seed0-attempt-002` |
| Game ID | `ls20-9607627b` | `ls20-9607627b` |
| Disposition | `partial` | `partial` |
| Final state | `NOT_FINISHED` | `NOT_FINISHED` |
| Completion genuinely observed | `false` | `false` |
| Levels completed / win levels | 0 / 7 | 4 / 7 |
| Non-reset actions | 6 | 754 |
| Resets | 1 | 4 |
| Total environment calls | 7 | 758 |
| Elapsed wall time | 581,375 ms | 28,809,905 ms |
| Integration ref | `182a00f17e4295d97de980e0bcb6eee8fada7717afcb183ca023ce87a1579139` | `fafd0b6d97489077155cfc1c7a07d3dd60ff9cb196d01147a25ded705ae2ceb0` |

Attempt 001's formal terminal disposition was `partial`. Its causal/operator
summary was `BLOCKED_INFRASTRUCTURE`: the current request was deadlocked by a
held irreversible-cost proposal that the memoized draft bridge forbade
replacing. No environment call occurred after the hold. Its unresolved burden
is that an official `WIN` was not earned before the stop.

Attempt 001 delivery identities:

- terminal record ref:
  `5153cb92e46cd844b8016efac24a42881f708347d5d80bbf3b19327cc1ddff78`;
- run seal SHA-256:
  `13ee0ad977d08fb448a777345affe650e156ee3fad8a6bd5b71156774e3d1fe4`;
- evidence-capsule manifest SHA-256:
  `0895b09305acc70c5109131612fb394f5faba22a2744ba050872a3a8a087f21c`;
- capsule verification: `true`.

Attempt 002 ended when a preregistered or known-no-effect boundary denied the
action. Its terminal incident is `KNOWN_NO_EFFECT:BudgetExceeded`. The elapsed
time was 9,905 ms beyond the preregistered 28,800,000 ms wall-clock allowance,
but the last confirmed pre-denial clock was 28,792,625 ms and no environment
call was admitted beyond the wall-clock ceiling. The terminal value includes
denial and terminalization overhead. Action, reset, and total-call counts
remained below their respective 2,048, 64, and 2,112 ceilings. Its unresolved
burden is that an official `WIN` was not earned before the boundary.

## Aggregate observation

Across the two distinct attempts, the records contain:

- 2 attempts, both `partial` and `NOT_FINISHED`;
- 0 observed `WIN` outcomes;
- 760 non-reset actions;
- 5 resets;
- 765 total official environment calls; and
- 29,391,280 ms total elapsed wall time (8 h 9 min 51.280 s).

These sums are descriptive across separate runs. They do not combine the runs
into one preregistered attempt or pool their budgets. The highest bounded
progress observed in either run was 4 of 7 levels.

## Numbered-successor integration repair

Attempt 001 exposed an integration deadlock: a held irreversible-cost proposal
could not be replaced because the memoized draft bridge treated the replacement
as the same draft. For attempt 002, the integration was repaired to issue each
replacement as a fresh, numbered successor. This preserved the held proposal's
history while giving its successor a distinct identity, allowing execution to
continue instead of attempting to overwrite the memoized draft. The differing
attempt integration refs above bind the two run configurations.

The repair is evidence that the specific proposal-lifecycle deadlock was
removed for the successor attempt. It is not evidence of `WIN`, autonomous
operation, hidden-game generalization, or an official score.

## Evidence-scale finding and delivery seal

The closed attempt-002 Strongwiz ledger is **3,855,728,640 bytes**, with 23,519
objects and 8,345 receipts. The legacy new-seal path requests **three logical
full snapshots**. Each snapshot performs two whole-SQLite SHA-256 passes around
a full row read and in-memory materialization. At the recorded size, that is up
to nine ledger-volumes, or **34,701,557,760 bytes**, before JSON parsing,
canonical re-encoding, allocation, paging, capsule output, or metadata overhead.
That estimate covers the new-seal phase alone; the complete legacy pack path
requests additional snapshots.

The frozen legacy verifier was stopped after more than an hour without a seal.
Immediately before termination it had reached about 18 GiB private memory on a
16 GiB host, more than 58 million page faults, and declining virtual-memory
headroom. It was read-only; the terminal record and SQLite ledger remained
unchanged, no run seal had been published, and no environment process or call
was involved.

A standalone bounded-memory post-run verifier was then added outside both
frozen source sets. Synthetic fixtures demonstrate byte-identical partial-run
v1 seal and capsule output relative to the legacy implementation; the large
real run cannot be compared with a completed legacy result because that path
did not finish. The repair streams rows and opaque files using a disk-backed
closure index, exclusive finalizer lock, pinned source rechecks,
finalization-time terminal/domain binding, no-clobber publication, receipt
readback, and owned rollback. Its source SHA-256 before real-ledger execution is
`4c00f2ea221c6ff63ddd288d31389878f93b889052310dec261ca8c0a717bc0f`.
This post-run tool is a verifier repair, not part of the frozen gameplay system.

The real bounded-memory finalization completed with exit code 0 in 8,014.951
seconds. Sparse process telemetry observed no more than approximately 142 MiB
private memory; this is a sampled value, not an instrumented peak. The committed
delivery identities are:

- attempt-002 run seal SHA-256:
  `d944c57f38f63d11bea711928498fda63c03b72f127ef0390d75059304d201d4`;
- attempt-002 evidence-capsule manifest SHA-256:
  `803a01fd841271e31983326380e65592a0f5235e5ba681670a522c33ad8814b7`;
- attempt-002 external run-receipt SHA-256:
  `0c5b454828b6bb9cadd5707cd2278698e12aec87d0d693657e128f20ffc17601`;
- source ledger SHA-256:
  `8345a6e66dbf1b45eacd1687390e39f6bdddc8701ec3129d6901e850d788dedd`;
  and
- finalizer-internal staged and published capsule verification: `true`.

An independent full published-capsule readback passed with the exact expected
capsule identity in 2,546.814 seconds. Its peak working set was 147.1 MiB and
sampled peak private memory was 134.9 MiB. Receipt pointer, hash, size,
canonical-form, seal, capsule, and terminal-reference consistency passed. No
stale finalizer/index/staging artifacts or source-ledger WAL, SHM, or journal
remained. A final independent SHA-256 pass over the closed source ledger again
returned `8345a6e66dbf1b45eacd1687390e39f6bdddc8701ec3129d6901e850d788dedd`.
The terminal outcome remains `PARTIAL`, `NOT_FINISHED`, with
`completion_genuinely_observed=false` regardless of packaging status.

The repair has explicit limits. It is not globally crash-atomic or fully durable
across its three publication destinations; abrupt process or power loss can
leave staging or lock artifacts that require inspection. Privileged Windows
path mutation remains outside its guarantee, same-volume hard links are
required, and Windows symlink tests skip where the host cannot create them. The
standalone portable verifier validates the generic domain projection and bound
terminal object/receipt; semantic comparison of the domain terminal record to
the run seal is a finalization-time check, not a property of that standalone
verifier alone.

## Verification gates

- full test suite: **313 passed, 4 skipped**;
- Ruff lint: passed;
- Ruff formatting check: 92 files already formatted;
- strict mypy: passed for 39 source files;
- frozen runtime recheck: 41/41 source files matched their recorded sizes and
  SHA-256 identities;
- frozen Strongwiz toolbelt diff: zero changes from baseline tree
  `9e58cb361919fca3638b1f76a00379740c4e4aa4`;
- tracked public receipt index: 8/8 exact copies matched size and SHA-256;
- candidate publication secret scan: no matches; and
- independent published-capsule streaming verification: passed.

## Owner-directed next-build hypotheses

The following ideas were formed after observing this calibration. They were not
available to the action selector and are not explanations retrofitted into the
measured run.

### Kevin Speak v0.1 — Adaptive Reversible Ledger Shorthand

The first shorthand experiment is named **Kevin Speak v0.1**. Its purpose is to
reduce reasoning-ledger volume without changing the underlying reasoning or
discarding source evidence.

The proposed separation is:

- raw evidence remains lossless, content-addressed, chunked, compressed, and
  independently hash-bound;
- the working ledger records compact deltas and references through a run-local,
  model-authored codebook;
- every grammar, symbol definition, and revision has a numbered version and
  explicit predecessor;
- every compact entry binds the exact codebook, grammar, and decoder artifact
  used to interpret it, and later revisions cannot reinterpret earlier entries;
- a compaction is accepted only when decoding under the bound grammar reproduces
  the canonical entry exactly; and
- distinctions that the current shorthand cannot carry remain in an explicit
  uncompressed residual lane until a later grammar extension earns them.

This makes compression falsifiable. A useful first gate is exact round-trip
reconstruction plus measured ledger-size, action-latency, verification,
codebook-transport, and codebook-validation costs against the uncompressed
baseline. Compression, frequency, or reuse does not increase a claim's truth or
authority.

### Adaptive calibration curriculum

Instead of beginning with another eight-hour run, the proposed development
sequence is:

1. 30 minutes for baseline behavior and codebook genesis;
2. 60 minutes for mechanics acquisition and codebook adaptation;
3. 90 minutes for deeper planning and an attempted finish; and
4. one final run that either pursues completion with the frozen learned stack or
   stops and returns to the owner for reassessment.

If later runs inherit shorthand or mechanics, the sequence is an adaptive
curriculum, not a collection of independent clean-room trials. Any later
generalization claim therefore requires a separately frozen candidate and an
unseen evaluation boundary.

### Lightweight steering heartbeat

A small ephemeral control-plane heartbeat can make long work naturally
steerable without bloating the evidence ledger. It should expose only the
current phase, last durable checkpoint, active gate, budget position, and
whether steering is safe. Only material state transitions need durable
receipts; routine pulses remain derived interface state.

The model-facing pulse may be lossy and disposable without requiring the
underlying boundary event to be either. A future **A0BK-informed candidate**
could separate three layers:

1. immutable source evidence and durable transition receipts;
2. a compact, predecessor-linked boundary witness that is complete for the
   declared steering contract, including current residual and authority; and
3. an ephemeral human-facing rendering of that witness.

The rendering can disappear while its meaningful cut and trace remain
recoverable. It is allowed to be lossy relative to the whole run, but must be
lossless relative to the fields promised by the steering interface. The
owner's observation that frequent small pulses appeared to preserve smooth
steering without an obvious usage increase is a candidate efficiency mechanism,
not a measured token or cost result. A later test should compare reorientation
work, repeated narration, steering latency, and model usage under matched work.
Any steering response that changes execution would be a consequential crossing
and must receive a durable receipt binding the displayed witness, supplied
authority, human intervention, and resulting policy change, even when unchanged
pulse renderings remain ephemeral. No exact A0BK source/version is bound here,
and this calibration did not implement or test A0BK conformance.

## Report evidence and inputs

This report was prepared from:

1. `docs/calibrations/001-strongwiz-arc3-clean-room.md`
2. `docs/calibrations/001-preregistration.json`
3. `artifacts/local/calibration-001/run.receipt.json` (attempt 001)
4. `playground/calibration-001-attempt-002/run/state/domain/terminal.record.json`
   (attempt 002)
5. the two run seals and evidence-capsule manifests;
6. the frozen legacy sealing implementation and the standalone streaming
   verifier plus its tests;
7. process and filesystem telemetry gathered during the two post-run
   finalization attempts; and
8. owner observations and design hypotheses explicitly labeled post-run above.

Publication-safe exact copies of the concise terminal records, run seals,
capsule manifests, and delivery receipts are stored under
`docs/calibrations/receipts/001/`. Raw frames, traces, official recordings,
ledger contents, complete capsules, game source, and capabilities remain local,
ignored, and outside the publication set.
