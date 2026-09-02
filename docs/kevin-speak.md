# Kevin Speak

Kevin Speak is an experimental, model-authored shorthand for bounded working
ledgers. It compresses a representation, not the reasoning standard behind the
representation. Canonical source identity, evidence requirements, uncertainty,
terminal authority, permissions, and grants remain outside the shorthand.

The feature is inert by default. Enabling Kevin Speak as a storage codec does
not by itself authorize presenting shorthand to a model.

Each workspace freezes a typed configuration. It chooses either
`decoded_storage`, where the model still receives reconstructed ordinary
contracts, or `model_facing`, where shorthand may enter model context as a
separate behavioral experiment. It also binds active-symbol, entry-size, and
incremental-codebook limits plus the exact promotion policy. Exact round-trip
and the residual lane cannot be disabled.

## Blank floor

A clean Kevin Speak workspace begins with an empty version-zero codebook. It
does not contain a vocabulary selected by Strongwiz, a previous task, or a
previous model. A model may propose symbols only after encountering material it
wants to abbreviate, and each proposal binds the exact source-payload references
that motivated it.

This gives the model an open notational surface without making model output
executable. A proposal contains a token, a literal expansion, a concise meaning,
and provenance references. Strongwiz decides only whether the data satisfies
the declared mechanical gates.

## Fixed decoder and immutable codebooks

Every codebook lineage binds the same fixed, nonexecuting decoder:

- ordinary Unicode text is literal UTF-8;
- `~TOKEN~` expands to the exact translation bound by the entry's codebook;
- `~~` represents one literal tilde; and
- no token invokes code, performs lookup outside the bound codebook, or acts on
  the environment.

Codebooks are immutable predecessor-linked revisions. Version zero is blank.
Each later revision names its exact predecessor and may define, replace, or
retire symbols. A replacement identifies the exact definition it supersedes.

A new expansion may itself use symbols, but it is decoded only through the
predecessor codebook. This permits recursive compression across revisions while
preventing self-reference, forward reference, or a later definition from
changing the meaning of an earlier entry. Every entry retains its exact
codebook version and decoder artifact reference, so an old entry is always
decoded under the language that existed when it was written.

Revision is additive history, not mutation. Retired and superseded definitions
remain in the lineage so receipts can still be reconstructed.

## Exact reconstruction and the residual lane

Before storage, the payload is serialized as canonical JSON and assigned its
content digest. The deterministic encoder finds a minimum-byte representation
under the selected codebook. It uses a symbol only when doing so is strictly
smaller than literal UTF-8.

An entry then takes one of two lanes:

| Lane | Admission condition | Stored representation |
| --- | --- | --- |
| `compact` | The shorthand is strictly smaller and the fixed decoder reconstructs the exact canonical bytes and digest. | Encoded text, source digest, codebook version, decoder identity, and measured sizes. |
| `residual` | No symbol matches, shorthand is not smaller, or the material otherwise cannot earn compact admission. | The canonical source object plus an explicit residual reason. |

The residual lane is part of the design, not a failure to finish the vocabulary.
A consequential distinction that does not fit the current grammar remains
available without abbreviation. The codebook may later be revised, but the old
residual receipt is not rewritten.

Exact round-trip verification establishes byte reconstruction only. It does not
establish that a phrase is true, complete, safe, relevant, or understood by a
model.

## Evaluation and cost accounting

A candidate codebook is evaluated against declared samples split into two
roles:

- **adaptation cases** may motivate or tune the candidate; and
- **validation cases** test it after adaptation and should not reuse adaptation
  sources when the promotion policy forbids leakage.

Each case records source bytes, predecessor-representation bytes,
candidate-representation bytes, and exact round-trip status. The evaluation
also charges the incremental serialized codebook. Gross content savings and net
savings are retained separately. A candidate can remain `not_earned` even when
some examples become shorter.

There are two accounting surfaces:

1. Local promotion charges adaptation, validation, and incremental codebook
   bytes on the declared suite.
2. A successor-stage comparison must additionally charge the complete
   transferred lineage, fixed-decoder identity, recommendation, optional
   review, adoption decision, and validation evidence.

A local net saving is therefore not automatically a campaign-level saving.
Transport and validation may displace the apparent benefit. Reports must retain
both denominators rather than folding those costs into an unqualified
"compression improved performance" claim.

Promotion makes a candidate available for future entries in the current
workspace. It neither approves inheritance nor changes any prior entry.

## Storage codec versus model-facing experiment

Kevin Speak has two distinct experimental uses:

- **Storage-codec use** measures retained bytes, transport cost, exact
  reconstruction, and bounded-memory behavior. The model may continue to see
  the ordinary decoded contract.
- **Model-facing use** presents the compressed notation to a model as part of
  its working context. Even with an exact decoder, this can change attention,
  interpretation, action selection, and error modes.

The second use requires its own explicit feature enablement, preregistered
budget, baseline, and ablation. It must compare otherwise matched runs and
preserve failures. Codec correctness is not evidence that model reasoning is
unchanged or improved.

## Successor recommendation, review, adoption, and transfer

Cross-run shorthand moves through five separate boundaries.

1. **Source-agent recommendation.** During work or closeout, the source agent
   may recommend the codebook it actually developed. The recommendation binds
   the last durable evidence boundary before that recommendation, the
   recommending driver, exact effective definitions, eligible evaluations,
   rationale, and known residuals. Its status is `recommended_not_approved`;
   it grants no successor use.
2. **Post-seal recommendation bundle.** After the source run is immutable, a
   portable bundle binds that recommendation to the final run seal and evidence
   capsule. This avoids a circular hash in which an in-run recommendation would
   try to contain the seal that contains it.
3. **Optional review or refinement.** A separate handoff workspace lets a
   later, possibly stronger model inspect the bundle, retain it, or propose a
   descendant codebook without editing the source specimen. A refinement stays
   on the same immutable lineage and needs its own eligible evaluation. The
   review is advisory and remains `reviewed_not_adopted`.
4. **Control adoption.** A supplied control source makes a scoped decision for
   one named target stage. It binds the exact candidate, recommendation,
   optional review, and evaluations. Model confidence, compression ratio, and
   review wording cannot manufacture this decision.
5. **Sealed transfer.** Only an approved adoption can produce a transfer. The
   transfer contains the post-seal bundle, complete genesis-first codebook
   lineage, review evidence, and adoption decision. The successor opens in
   `explicit_inheritance` mode rather than pretending to have a blank ledger,
   and must present the exact target-stage identity named by that adoption.

Definition status is explicit at the adoption boundary:

| Status | Successor meaning |
| --- | --- |
| `approve` | May encode new working entries in the named successor stage. |
| `reject` | Must not enter the successor's active vocabulary. |
| `defer` | Remains provisional and inactive until another scoped decision. |
| `historical_only` | Travels only when required to reconstruct immutable ancestry; it is not active vocabulary. |

An advisory review may use `recommend` for an effective definition, but only
control adoption may convert that advice to `approve`. Transfer fails closed
unless every effective definition is explicitly approved and every inactive
ancestor needed for reconstruction is explicitly `historical_only`. Rejected
and deferred alternatives are retained as both immutable definitions and
decisions, not silently absorbed into the active codebook or reduced to opaque
identifiers.

The transfer explicitly excludes action sequences, domain state, private
reasoning, and authority. It carries a working representation and its evidence,
not a solution or a grant to act.

## Claim and authority ceiling

No Kevin Speak token can abbreviate away a missing witness, unresolved burden,
uncertainty marker, consent boundary, permission, authorization, or terminal
condition. A shorthand occurrence inherits the status of the decoded source;
repetition or compactness does not strengthen that status.

Models propose notation. Mechanical checks establish exact reconstruction and
declared byte counts. External control decides scoped adoption. The domain or
other named authority remains responsible for terminal truth.

## Historical boundary

Calibration 001 is immutable historical evidence and predates Kevin Speak.
Neither its runtime nor its result is retroactively relabeled as a Kevin Speak
run. Compatibility tests exercise Calibration 001 against its frozen historical
tree, while the evolved Strongwiz kernel is expected to fail that calibration's
pinned-baseline gate. Any Kevin Speak calibration must have a new run identity,
preregistration, frozen runtime, and receipts.

The runnable non-ARC mechanism example is
[`examples/kevin_speak_campaign.py`](../examples/kevin_speak_campaign.py). It
labels itself synthetic and deliberately does not claim that showing shorthand
to a model improves behavior.
