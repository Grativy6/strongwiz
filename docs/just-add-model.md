# Just-add-model boundary

Strongwiz makes the model-provider seam small enough that a local model can
be attached without rebuilding the laboratory. The stable unit is the
observation/action/memory/receipt contract, not a particular model runtime.

## What the model supplies

A model receives one immutable `ReasoningRequest` and returns zero or more
`ProposalDraft` values. Each draft contains:

- one action candidate;
- one goal-linked, decision-relevant distinction;
- a falsifiable prediction and alternatives;
- concise rationale and evidence references;
- heterogeneous progress, information, risk, and resource costs.

The model does not supply control state, permission, authorization, execution
receipts, or terminal status. Strongwiz binds every draft to the exact driver,
observation, scope, and goal before it becomes a `CandidateProposal`.

An integration may also let the model propose `KevinSymbolProposal` values and
a next-round shorthand recommendation. Those remain declarative data. The fixed
decoder, validation suite, and cost gates are mechanical; optional later-model
review is advisory; and a separately supplied control decision determines the
exact definitions, if any, admitted to a named successor stage. See
[`kevin-speak.md`](kevin-speak.md).

## In-process model

`CallableModelDriver` accepts an ordinary Python function:

```python
def propose(request: ReasoningRequest) -> tuple[ProposalDraft, ...]:
    return (my_draft(request),)


driver = CallableModelDriver(
    driver_id="my-model",
    driver_version="1",
    driver_artifact_ref=model_and_configuration_sha256,
    proposal_function=propose,
)
```

The artifact reference should bind everything that could change action choice:
weights, tokenizer, prompt or symbolic rules, configuration, and provider
adapter source.

## Offline process model

`FramedModelDriver` connects to binary reader/writer streams. The wire format is
versioned canonical JSON with a fixed header, unsigned 64-bit length, SHA-256,
strict UTF-8, configured payload ceiling, and exact partial-read/write loops.
Requests and replies carry distinct message identities and exact reply
bindings. A bounded replay guard rejects duplicate identity, identity reuse,
and identical payload replay within its declared window.

Construction requires an explicit `FramedModelRestartState`:

```python
from strongwiz import FramedModelDriver, FramedModelRestartState

restart_state = FramedModelRestartState.initial(
    session_id="my-run:model-session-1",
    driver_id="my-model",
    driver_version="1",
    driver_artifact_ref=model_and_configuration_sha256,
)
driver = FramedModelDriver(
    driver_id="my-model",
    driver_version="1",
    driver_artifact_ref=model_and_configuration_sha256,
    reader=model_stdout_binary,
    writer=model_stdin_binary,
    restart_state=restart_state,
    state_sink=persist_restart_state_atomically,
)
```

The state reserves each request sequence before outbound I/O and retains the
bounded identities of accepted responses. To claim crash durability, the
integration must persist every value supplied to `state_sink` before the
callback returns, then reconstruct the driver with the latest value. Strongwiz
does not launch, monitor, or restart the operating-system process.

This works with local subprocess pipes, an in-process byte channel, or another
offline binary transport. It never depends on terminal line length, a TTY, or
newline-delimited input. Strongwiz does not start an arbitrary provider process
or bundle model weights; the integration owns that lifecycle and freezes its
artifact identity in the run manifest.

## Conformance before work

`check_model_driver` invokes a model once on a declared fixture and checks
identity, output type, proposal uniqueness, exact request bindings, and the
visible action aperture. `check_domain_adapter` checks repeatable
normalization, action contracts, terminal-authority typing, and an optional
exact before/action/after outcome translation.

A passing report establishes only that structural fixture. It does not prove
reasoning quality, determinism, domain completeness, permission, or safety.

## What still must be supplied

For advisory work, a model and observations may be enough. For consequential
work, the configured product must also supply:

1. a domain adapter that owns observation translation and terminal authority;
2. a single-writer executor;
3. a control snapshot and externally rooted task grant;
4. applicable PEA, PECAN, and SEED records;
5. explicit action, time, memory, and other resource ceilings.

This is intentional. A general laboratory can make the model replaceable, but
it cannot infer a domain's authority, credentials, consent, legal terms, or
success definition.

The end-to-end non-ARC example is
[`examples/reference_counter_lab.py`](../examples/reference_counter_lab.py).
