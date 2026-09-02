# Calibration 003 preparation — Strongwiz v3, PAL v2.3, and the scribe

Status: **PREPARED, NOT RUN, NOT YET PREREGISTERED AGAINST A FROZEN TOOLBELT**.

No ARC environment was acquired or contacted while preparing this build. No
game action, reset, credential, account, competition entry, terms acceptance,
submission, or score was used. A later run requires a separate authorization
and a preregistration binding the final implementation commit and tree.

## Question

Can a dedicated representation-only scribe, inside PAL v2.3 typed transport
boundaries, reduce total representation/reorientation cost without reducing an
otherwise matched Strongwiz operator's evidence quality or ARC progress?

The completion goal of any later ARC execution remains the official
environment state `GameState.WIN`. `NOT_FINISHED` requires continued work while
the declared budget remains; `GAME_OVER` is failure evidence and permits only
the recovery the official environment allows. A level transition, score,
confidence, synthetic pass, or complete-looking map is not completion.

## What Calibration 002 actually showed

Calibration 002 is an immutable `PARTIAL`, not a failed receipt and not a v3
baseline retcon:

| Measure | Calibration 002 observation | v3 consequence |
|---|---:|---|
| Best progress | 1 / 7 levels | Optimize discriminating probes and cumulative state, not raw action rate |
| Official terminal state | `NOT_FINISHED` | Preserve the authoritative completion rule |
| Non-reset actions | 699 | Match action denominators between arms |
| Resets / calls | 12 / 711 | Charge recovery and all official calls |
| Recorded elapsed | 10,751,405 ms | Report full campaign and environment time separately |
| Kevin entries | 10 | Give representation work to a dedicated role |
| Kevin promotions | 0 | Treat scribe efficacy as untested |
| Source / representation bytes | 4,234 / 4,234 | Count every representation cost, not just source entries |
| v2 ledger size vs v1 attempt 002 | about 89.1 times smaller | Retain bounded/reference-normalized evidence machinery |

V2 processed environment actions about 2.5 times faster than the historical v1
attempt, but the comparison was confounded and did not reach deeper progress.
Every stage reacquired level 1. Faster interaction therefore did not establish
better information efficiency, transfer, or reasoning depth.

The strongest v2 engineering gains were smaller ledgers, exact target-bound
transitions, streaming finalization, and honest residual fallback. The largest
unresolved seams were:

1. the action model also had to notice and author shorthand, so no adaptation
   cycle occurred;
2. heartbeat code existed but was not connected to the measured campaign;
3. fixed-duration stages advanced without an explicit evidence-yield gate;
4. restarting fresh environments imposed a replay tax while cumulative causal
   state was sparse;
5. the final handoff did not bind the separately published mechanics packet;
6. one semantic recommendation ID was concurrently reused for different
   objects; and
7. an interrupted review attempt could not resume from its partially written
   ledger.

These are observations or implementation mechanisms, not a causal explanation
of game performance.

## Improvement map from v2 to v3

| V2 residual | V3 repair under preparation | Evidence required before credit |
|---|---|---|
| The action operator never authored shorthand | Give a separately identified, representation-only scribe the adaptation task | At least one frozen draft and disjoint held-out evaluation; matched no-scribe arm |
| Four stages repeatedly reacquired level 1 | Keep one continuous environment lineage inside each arm; treat a stage change as bookkeeping, not an automatic reset | Exact checkpoint/re-entry receipts and no unreceipted replay or environment call |
| Fixed durations advanced despite weak information yield | Advance or reallocate only on a material evidence-yield disposition | Per-stage yield denominator and explicit low-yield decision receipt |
| High action throughput did not produce deeper progress | Rank probes by the decision they could change and charge repeated cousins | Evidence yield per action, repeated-probe rate, and official progress under equal budgets |
| Useful level-2 facts did not compound across fresh stages | Carry only concise, target-bound facts with evidence, uncertainty, and reopening handles | Packet hash/source verification and identical packet availability in matched successor arms |
| Final handoff omitted the separate mechanics packet | Bind every consumed carry packet in the run plan and campaign index | Exact packet ref, source-artifact hashes, target-stage binding, and explicit exclusions |
| A semantic recommendation ID was reused for two objects | Make semantic IDs idempotent for identical content and reject conflicting reuse | Conflict fixture plus append-only supersession path |
| Interrupted review could not safely resume | Journal requests and frozen drafts before cross-account mutation; stop for re-entry after ambiguous partial mutation | Crash-injection tests proving no repeated provider call or silent continuation |
| Heartbeat machinery was outside the measured campaign | Emit only at material boundaries and classify unchanged updates as administrative stutter | Zero timer-only progress events; separate work, progress, and audit coordinates |
| The ledger was much smaller, but Kevin Speak earned none of that gain | Preserve reference-normalized/streaming storage and charge every scribe/codebook byte separately | Matched total-cost report; no attribution to shorthand unless a promotion passes its frozen denominator |

The ordering is deliberate: first preserve evidence and restart integrity, then
test whether role separation activates shorthand, and only then ask whether it
changes reasoning or official progress. More actions per minute is not itself
an improvement target.

## V3 mechanisms under test

### Dedicated scribe

`strongwiz.scribe` separates the representation provider from the action
provider. Its declared request view contains only receipt-bound derived
adaptation summaries and omits held-out validation payloads. The existing fixed
Kevin decoder and promotion policy decide whether a proposal earned use. It has
no action or authority port. Failure preserves the pending material and its
exact decodability in the compact-or-residual lane already chosen at ingestion.
The in-process callable driver is trusted application code, not a
confidentiality sandbox; an untrusted scribe requires a separate-process
capability boundary.

The first measured mode is decoded storage. Model-facing shorthand is outside
this experiment so a language change cannot be confused with a storage or
role-allocation effect.

### PAL v2.3 transport profile

`strongwiz.pal23` makes the compared projection explicit, records boundary-role
adapters, separates administrative heartbeat stutter from productive changes,
uses immutable grant epochs, and expands checkpoint freeze/thaw receipts. A
restart never restores spent resources or expired authority. This is a
targeted adapter, not full PAL v2.3 conformance.

### Cumulative evidence without replay

The v2 carry packet records concise positive and negative mechanics with stable
identities, status, evidence boundary, counterevidence, and reopening handles.
It never contains frames, raw traces, action sequences, domain state, private
reasoning, or authority. Consuming it makes an arm an **adaptive successor** on
that public game, not a fresh generalization test.

The packet loader verifies every source artifact byte identity. A fact reference
is accepted only when it is the exact artifact digest or occurs in a
schema-known evidence field of a validated JSON source; arbitrary digest-shaped
text is not an evidence anchor. This establishes source-reference resolution,
not the semantic truth or sufficiency of the fact, which remains bounded by its
statement, uncertainty, and reopening condition.

### Event-yield curriculum

Wall time is a ceiling, not the advancement signal. A stage advances only at a
material boundary after one of these dispositions is receipted:

- a relevant mechanic or counterexample was retained;
- a competing transition hypothesis was discriminated;
- a path/access/resource/hazard residual was narrowed;
- an earned scribe evaluation changed the representation state;
- official level progress occurred;
- the evidence yield fell below the preregistered threshold and the next stage
  explicitly reallocates its remaining budget; or
- the stage reached a terminal or hard resource boundary.

Unchanged timer heartbeats do not count.

## Required matched design

The later preregistration must define at least two arms:

| Coordinate | No-scribe control | Dedicated-scribe candidate |
|---|---|---|
| Operator model/runtime | identical frozen identity | identical frozen identity |
| Domain/game/version | identical | identical |
| Seed and initial state | identical | identical |
| Action/reset/call/time/context/compute/memory budgets | identical | identical |
| Strongwiz/PAL toolbelt | identical | identical |
| Kevin decoder and starting codebook | identical blank state | identical blank state |
| Scribe driver | absent | frozen representation-only identity |
| Summary material | stored losslessly | stored losslessly and offered to scribe |
| Presentation mode | decoded storage | decoded storage |

Each arm starts in a physically distinct absent-or-empty lab root with its own
SQLite ledger and zero-state genesis. A campaign index outside those roots may
bind plans, identities, stage references, checkpoints, and seals only. It must
not become a shared payload ledger.

Inside one arm, a curriculum boundary must not itself recreate the official
environment. The same live environment lineage continues across stages unless
the official environment, the resource policy, or a receipted recovery rule
requires a reset. A checkpoint restores only the declared work projection and
revalidates every bound non-work coordinate; it never manufactures a new life,
budget, grant, permission, or authority.

A carry-packet diagnostic and a genuinely fresh arm answer different questions.
If budget permits both, preregister them separately. Never describe a same-game
carry-packet run as unseen generalization.

## Frozen hypotheses

- **H1, representation activation:** the dedicated-scribe arm executes at least
  one valid adaptation/held-out evaluation cycle; v2 executed none.
- **H2, integrity:** every source entry round-trips exactly and forbidden
  material/authority leakage remains zero in both arms.
- **H3, net representation cost:** after charging all named representation
  surfaces, any promoted codebook produces positive net savings on its frozen
  validation denominator. Otherwise H3 is `NOT_EARNED`.
- **H4, operator burden:** the scribe arm reduces measured reorientation work or
  operator-facing context under the same presentation mode without increasing
  factual loss or reopen failures.
- **H5, domain effect:** under matched budgets, the scribe arm changes action
  efficiency or official progress. One outcome is bounded evidence for this
  exact comparison, not generality.
- **H6, restart:** interruption and thaw do not repeat a frozen provider call,
  environment action, or semantic event ID, and do not restore spent resources
  or authority.
- **H7, heartbeat:** only material events emit updates; administrative stutters
  make no progress claim.

H1-H4 can be tested without ARC. H5 requires the later authorized public
environment run. Synthetic success cannot substitute for H5 or `GameState.WIN`.

## Measurements required in every arm

- official state, levels completed, win levels, actions, resets, calls, and
  authoritative completion observation;
- wall time, model latency, environment latency, context, compute, and memory
  high-water;
- new, supported, narrowed, reopened, rejected, deferred, and superseded facts;
- probe predictions, alternative summaries, residual channels, and evidence
  yield per action;
- source, residual, compact, request, response, codebook, evaluation, review,
  adoption, transfer, verification, heartbeat, checkpoint, and capsule bytes;
- scribe calls, failures, retries, proposals, evaluations, promotions,
  retirements, and exact round trips;
- operator reorientation count and latency; and
- every transport break, recovery, `GAME_OVER`, or budget refusal.

Report both totals and matched denominators. Do not credit Kevin Speak for v2's
ledger-size reduction, and do not credit the scribe for any v3 difference
without the matched arm.

## Preparation and freeze sequence

1. Finish and verify the generic PAL v2.3 and scribe modules.
2. Verify a preparation-only `calibration_003` harness. It may initialize,
   inspect, and synthetically preflight labs, but it has no ARC import,
   credential path, environment acquisition, or action command.
3. Commit the toolbelt. Record its exact commit, tree, package artifact, source
   registry, schemas, driver identities, and dependency lock.
4. In a later commit, preregister the exact frozen toolbelt and matched budgets.
   Any code change after that requires a successor preregistration.
5. Only after a separate owner instruction, initialize fresh run roots and use
   the authorized public environment.
6. Seal each arm independently, then perform review/adoption in a separate
   fresh review lab.

## Stop and claim rules

- `GameState.WIN` is the only completion condition for an ARC arm.
- `NOT_FINISHED` is not a win. Continue only while that arm's declared resources
  and authorization remain.
- `GAME_OVER` is preserved as failure evidence; reset or recover only when the
  environment and budget allow.
- A scribe failure does not stop the action operator; it preserves the pending,
  exactly decodable entries in their existing compact-or-residual lanes and
  remains an experimental failure.
- Cross-arm contamination, driver drift, ledger reuse, an unbound checkpoint,
  forbidden material, or an unreconciled transport break stops the implicated
  arm before further action.
- Owner pause remains authoritative and is receipted without converting a
  partial run into failure or success.

## Claim ceiling

Preparation establishes only that the v3 mechanisms and comparison are ready
for a later frozen evaluation. It is not an ARC result, a contest entry, an
autonomous-offline package, a PAL v2.3 conformance result, a causal performance
claim, or evidence of general intelligence.
