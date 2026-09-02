# Adaptive calibration campaigns

Strongwiz supports a sequential calibration curriculum for mechanisms that
need more than one bounded run to become useful. It is an adaptive development
campaign, not a collection of independent clean-room trials. The first stage
establishes a blank baseline; later stages may inherit only material carried by
an explicit, validated transfer.

The campaign fixes one overlying objective and one named terminal authority.
Each stage may form smaller goals and investigate consequential distinctions,
but those local choices remain subordinate to the campaign objective.

## Four-stage default

The default curriculum is 30/60/90/final:

| Stage | Bound | Mode | Purpose and inheritance |
| --- | ---: | --- | --- |
| 1 | 30 minutes | Baseline | Establish behavior from a blank Kevin Speak codebook and no inherited mechanics. |
| 2 | 60 minutes | Acquire | Acquire mechanics, evaluate shorthand adaptations, and use only an explicitly approved transfer from Stage 1. |
| 3 | 90 minutes | Deepen | Improve planning and test the inherited learned stack against harder residuals. |
| 4 | Caller-declared | Finish or reassess | Freeze the selected stack and pursue the terminal objective; finish only on authoritative success, otherwise return for reassessment. |

The final bound is supplied before that stage starts. A campaign may stop early
if its terminal authority genuinely reports success. A promising transition,
high score, reduced residual count, or exhausted wall-clock budget is not
terminal success.

## Every stage is a separate sealed run

Only one stage may be active. Each stage receives its own:

- run identity, resource budget, and occurrence;
- frozen toolbelt, model-driver, domain-adapter, policy, and configuration
  references;
- lab genesis and run specification;
- raw and derived evidence ledger;
- terminal disposition and terminal-state evidence;
- run seal and complete evidence capsule; and
- stage handoff binding progress evidence and the next decision.

The next stage cannot begin merely because the previous process ended. Its
predecessor must be sealed, its handoff must say `advance`, and its learned
stack must be admitted through an exact transfer to the named target stage.
This makes interruption, replacement of a model, and later audit distinguishable
from silent continuation of one mutable run.

Inspection and analysis after sealing should operate on the capsule or a
disposable copy. They may produce new derived artifacts, but they do not rewrite
the sealed specimen.

## Learned-stack transfer

A successor may receive bounded learned state rather than starting from zero.
The transfer identifies the source handoff, source run seal, target stage,
validation evidence, and each admitted mechanic or other fact.

Kevin Speak inheritance additionally requires both:

- a sealed shorthand transfer containing the source-agent recommendation,
  post-seal recommendation bundle, optional smarter-model review or refinement,
  and complete codebook lineage;
  and
- the scoped control-adoption decision that states which definitions are
  approved, rejected, deferred, or historical-only for this target stage.

The successor therefore knows not only the vocabulary it may use, but also the
alternatives that were considered and withheld. A model review remains advice;
only the bound control decision admits working representation.

The source recommendation is written before its run seal and binds the last
durable evidence boundary. After sealing, a separate handoff workspace imports
the recommendation bundle and performs any review, refinement, and adoption.
It never reopens or appends to the sealed source ledger.

A learned-stack transfer excludes action sequences, authorization, domain
state, and private reasoning. It transfers earned representation, mechanics,
and cited facts—not a replay script, hidden solution, environmental snapshot,
or permission to act. Authority is revalidated independently at every
consequential boundary.

Because Stages 2 through 4 intentionally inherit state, their results cannot be
reported as independent clean-room generalization trials. Their proper claim is
performance within one declared adaptive campaign.

## Stage decisions

A stage handoff records its terminal disposition, terminal state, genuine
completion marker, progress evidence, active codebook, retained mechanics, and
one next decision:

- `advance` starts the next declared stage through a transfer;
- `finish` is legal only when the named terminal authority genuinely reported
  success; or
- `reassess` stops execution and returns the campaign to its controlling user or
  process with the residuals intact.

The final stage has a hard two-way boundary: authoritative success produces
`finish`; every nonsuccess outcome produces `reassess`. It must not loop itself,
silently extend its budget, or manufacture a smaller success criterion.

For an ARC-AGI-3 calibration whose success condition is `GameState.WIN`, only
the official environment's exact `WIN` state can close the campaign. This is an
example of a domain-specific authority binding, not a Strongwiz-wide definition
of success.

## Event-driven heartbeat

Long stages may expose a small steering view without creating a second ledger.
The heartbeat is event-driven. It has no timer input and does not emit merely
because time passed or because a conversational ping feels socially expected.

A durable heartbeat boundary may be produced when at least one declared field
materially changes, such as:

- phase or active gate;
- latest checkpoint;
- budget, steering-aperture, or risk band;
- residual set; or
- terminal state.

If none changes, the update is suppressed. A silence-breaking liveness view is
allowed only when it binds fresh observable evidence with an increasing progress
ordinal. Repeating old evidence or restating the same state is not liveness.

The rendered heartbeat is deliberately lossy and disposable. It is complete
only for its declared steering fields and carries no authority. Material state
changes receive predecessor-linked durable witnesses; a liveness-only view does
not pretend to be a new reasoning checkpoint. Any steering instruction that
changes policy must bind the displayed view, an externally supplied authority
reference, the instruction, and the before/after policy references in a
separate receipt.

This keeps steering responsive without converting periodic narration into
evidence, consuming attention for its own sake, or allowing a dashboard to
replace the durable run record.

## What each stage should measure

Stage reports should retain at least:

- wall time, action or experiment count, memory high-water mark, and terminal
  disposition;
- exact frozen-runtime, model-driver, domain, seed, and budget identities;
- Kevin Speak source bytes, representation bytes, codebook bytes, validation
  bytes, and transfer bytes when enabled;
- compact versus residual counts and exact round-trip results;
- mechanics or facts retained, reopened, rejected, or deferred;
- recommendation, review, adoption, and transfer references; and
- unresolved failures and the smallest stated reopening condition.

Matched comparisons should distinguish storage savings from changes in model
behavior. If shorthand is shown to the model, compare it with a decoded-context
baseline under the same model, task surface, and resource limits. A campaign
that adapts between stages may measure improvement over its own baseline, but
it does not supply an independent generalization denominator.

## Historical boundary

Calibration 001 remains a frozen historical run with its original source,
preregistration, receipts, result, and claim ceiling. The adaptive curriculum,
Kevin Speak, and event-driven heartbeat are later mechanisms. They do not repair,
extend, or reinterpret Calibration 001. A campaign using them starts under a
new identity and records exactly what it inherited.
