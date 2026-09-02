# Calibration 002 — Result

Status: **PARTIAL; official `GameState.WIN` was not observed.**

The owner requested an early resource pause during Stage 4 after useful bounded
evidence had been collected. The live control endpoint was closed, the fourth
run was terminalized, all four runs were sealed, and the adaptive campaign was
closed with the decision `reassess`. No further environment action was sent.

## Frozen scope and claim ceiling

Calibration 002 tested the Strongwiz v2 toolbelt frozen at commit
`1e6c2478cbb4f4168d8ec8857b65b88af1fd499a` (tree
`83172b2de0712af0e591486ff3e6d0550cea5691`) with Python 3.12,
`arc-agi==0.9.9`, `arcengine==0.9.3`, seed `0`, and exact public game
`ls20-9607627b`. Campaign implementation and transition hardening were
checkpointed at `5ba8867af49a58c59f75e0c3673b2f9156707d40` before the measured run.
Each run seal independently binds the exact integration bytes it used.

The preregistered campaign divided Calibration 001's aggregate ceilings among
four fresh labs of 30, 60, 90, and 300 minutes. Success required the official
environment itself to report `GameState.WIN`. It never did. The bounded result
is therefore:

> Four fresh, Codex-operated Strongwiz v2 stages in one adaptive local-public
> campaign each ended `PARTIAL` and `NOT_FINISHED`. Each reached 1 of 7 levels.
> Across the campaign, 699 non-reset actions, 12 resets, and 711 official calls
> were recorded. Stage 4 stopped at the owner's resource pause. Completion was
> not genuinely observed.

This was not a competition entry, Kaggle result, private or official score,
autonomous-offline evaluation, or generalization test. No account, owner
credential, submission, competition entry, terms acceptance, purchase, or
merge was used.

## Exact stage results

| Stage | Mode | Stop | State | Levels | Actions | Resets | Calls | Recorded elapsed |
|---|---|---|---|---:|---:|---:|---:|---:|
| 1 | baseline | calibrated stage stop | `NOT_FINISHED` | 1 / 7 | 118 | 2 | 120 | 1,699,453 ms |
| 2 | acquire | wall-clock stop | `NOT_FINISHED` | 1 / 7 | 122 | 1 | 123 | 3,256,265 ms |
| 3 | deepen | hard action boundary | `NOT_FINISHED` | 1 / 7 | 384 | 7 | 391 | 4,462,329 ms |
| 4 | finish or reassess | owner resource pause | `NOT_FINISHED` | 1 / 7 | 75 | 2 | 77 | 1,333,358 ms |
| **Total** | one adaptive campaign | `reassess` | `NOT_FINISHED` | **best 1 / 7** | **699** | **12** | **711** | **10,751,405 ms** |

The elapsed total is the sum of the four terminal records: 2 h 59 min
11.405 s. It is not a claim that all wall time outside the environment
controllers was measured. Stage 3 records the incident
`KNOWN_NO_EFFECT:BudgetExceeded`; no action crossed its hard action limit.
Stage 4 used only part of its preregistered allowance because the owner chose
to preserve remaining resources.

## Kevin Speak result

The safe representation boundary worked, but no shorthand was invented.

| Stage | Entries | Residual | Compact | Evaluations | Source bytes | Representation bytes |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 2 | 2 | 0 | 0 | 1,635 | 1,635 |
| 2 | 2 | 2 | 0 | 0 | 634 | 634 |
| 3 | 3 | 3 | 0 | 0 | 1,090 | 1,090 |
| 4 | 3 | 3 | 0 | 0 | 875 | 875 |
| **Total** | **10** | **10** | **0** | **0** | **4,234** | **4,234** |

Every recorded representation round-tripped exactly. The active codebook
remained blank version 0 at ref
`08fc1422b7a86a2e126681202a3394aa819daa171724bd22c0351ea0a2b90932`.
There were no adaptation attempts, codebook evaluations, promoted symbols, or
byte savings. Accordingly, this campaign provides no evidence that Kevin Speak
compression improves reasoning, memory, speed, or play.

The three successor transitions separately reviewed, target-bound, adopted,
and verified the unchanged blank representation plus allowed retained-mechanic
statements. Frames, raw traces, action sequences, domain state, private
reasoning, and authority did not cross. The transition-manifest identities are:

- Stage 1 to 2:
  `df2ec2dddd0ce2383ce08c799ac4a089914e0fc1655a8d9ce8faa251a6531038`;
- Stage 2 to 3:
  `246e5aa6ec40f6104546d51e8c9ec43f58b349cc0017d3fe2647eb824e63431d`;
  and
- Stage 3 to 4:
  `66de57d5ea94847804f7ef39ae919ec8a835ca394d04ae452809c4b0deeaa276`.

The closed learning ledger has 37 receipts with head
`a253b24742f4b39f3e15c01000ad9810c949a1948f3e28f737f708d07ec8424e`.
Its campaign ref is
`bb96addfde48847b083a52d36f2a6abd22189232af03c10a59fbffbf81647f6a`,
and final checkpoint ref is
`3fbee5cd92812fbb77ba5d1bba37b197ede6ab5ecc179af460edaf4ef4ade8c1`.

## Run-local mechanics and residuals

The following are bounded observations from this exact public game and may be
useful for diagnosing the controller. They are not hard-coded production rules
or evidence of cross-game generality:

- cardinal inputs repeatedly moved the controlled five-by-five block by five
  pixels along legal corridors;
- on level 1, a rejected blue-socket contact, an intervening white contact, and
  blue-socket recontact produced the official transition to one completed
  level;
- each of two distinct yellow contacts observed on level 2 removed that ring
  and refilled the visible action bar; and
- the level-2 lower-yellow, white, upper-yellow history was rejected at the
  blue socket.

The accepted level-2 contact history and every later level remain unresolved.

## Historical comparison with Calibration 001

Calibration 001 attempt 002 reached 4 of 7 levels in 754 actions, 4 resets, 758
calls, and 28,809,905 ms. Calibration 002 reached at most 1 of 7 levels in any
stage and was paused after 699 actions, 12 resets, 711 calls, and 10,751,405 ms
summed across four fresh runs.

This is descriptive, not causal. Calibration 002 was an adaptive four-stage
campaign, deliberately restarted the environment between stages, used a newer
toolbelt, and stopped early. It cannot establish that v2, the curriculum, or
Kevin Speak helped or hurt completion performance.

One engineering difference is nevertheless measurable. Calibration 002's four
run ledgers total 43,253,760 bytes, versus 3,855,728,640 bytes for Calibration
001 attempt 002: about 89.1 times smaller. This must not be credited to Kevin
Speak because every Kevin Speak entry remained uncompressed. The difference
combines storage architecture, run shape, action history, and other v2 changes.

## Post-run hypothesis: a dedicated scribe

The absence of invented shorthand suggests a role-allocation problem worth a
separate test. The same action-selecting model was asked to observe, infer,
plan, play, preserve evidence, and notice reusable language under time pressure.
It consistently spent its attention on play and left the representation in the
safe residual lane.

The next candidate should add a **dedicated Strongwiz scribe** between the
operator model and the durable representation layer. This is a post-run design
hypothesis, not a finding from the measured campaign. The scribe should:

1. receive only concise, explicit decision summaries and receipt-bound public
   evidence—not hidden chain-of-thought;
2. preserve novel material losslessly in the residual lane;
3. detect repeated canonical phrases or structures and propose shorthand;
4. bind every proposal to a versioned grammar, decoder, source identities, and
   exact reconstruction test;
5. evaluate adaptation and validation cases separately, charging codebook
   transport and verification costs; and
6. recommend, but never authorize, the next round's codebook.

The action operator remains responsible for decisions. The scribe cannot act in
the environment, grant authority, improve a claim merely by compression, or
reinterpret prior entries. Its first experiment should compare matched runs
with and without the scribe and measure compact-entry rate, exact round trips,
net bytes, operator reorientation work, action latency, and game progress.

## Orchestration findings

The pause exposed one small identity bug in the experimental workflow. The
operator and the parent finalizer concurrently recorded two different advisory
objects using semantic recommendation ID `stage4-reassessment-001`. The final
handoff selected the operator's earlier recommendation object
`a053a8ddc7dce707c9d4a8f906691e7b1703d35a968cf00d0e4d0e19a016210f`.
The later object remained unselected evidence and did not affect transfer or
the final decision. A successor should require recommendation-ID uniqueness,
idempotent writes, or explicit supersession links.

An earlier transition-review worker also stopped after writing its request.
The request was preserved, then processed through a fresh canonical review
ledger whose target binding and exclusions verified. Failed-attempt ledgers
remain local forensic artifacts and are not presented as canonical reviews.

## Evidence boundary

Exact copies of the concise terminal records, run seals, evidence-capsule
manifests, delivery receipts, transition receipts, and final handoff artifacts
are under `docs/calibrations/receipts/002/`. The terminal identities are:

| Stage | Terminal record | Run seal | Capsule manifest | Delivery receipt |
|---|---|---|---|---|
| 1 | `71281405…7424fbe` | `f45f7dd2…312d1a5a` | `87a25cab…1edcdc0` | `1295f531…75dc6ca8` |
| 2 | `f40b2805…b601de` | `277db6c4…96f2323` | `0b199305…9fa9d0` | `fd05332b…203f5a2` |
| 3 | `de767815…ac6f86` | `ca9e11a3…32f22` | `f019ad03…aa2c90` | `1a0e9783…ecd9aa3` |
| 4 | `456c6d01…b99d7a` | `8b70bce8…625142` | `3473cce4…73c16d` | `16dde5fb…100f78` |

Raw frames, raw traces, official recording contents, SQLite ledgers, complete
capsules, game source, and capabilities remain local and ignored. Their hashes
and sizes are retained in the published metadata, but this Git receipt set alone
cannot replay or independently inspect omitted raw evidence.

## Final repository verification

- full test suite: **391 passed, 4 skipped**;
- Ruff lint: passed;
- Ruff formatting check: 118 files already formatted;
- strict mypy: passed for 46 source files;
- frozen Strongwiz toolbelt diff from commit
  `1e6c2478cbb4f4168d8ec8857b65b88af1fd499a`: zero changes under
  `src/strongwiz`;
- public receipt index: 31 exact copies and 1 derived campaign summary matched
  every recorded size and SHA-256 identity;
- terminal-record recomputation: 699 actions, 12 resets, 711 calls,
  10,751,405 ms, best progress 1/7, four `NOT_FINISHED` states, and zero
  observed wins; and
- candidate credential/private-key pattern scan: zero matches.
