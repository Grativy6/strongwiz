# Claim boundary

Strongwiz is an experimental reasoning laboratory and a set of typed,
inspectable mechanisms. The current repository does not establish:

- artificial general intelligence, consciousness, personhood, or moral
  standing;
- a general theory of intelligence or guaranteed improvement for any model;
- correctness, truth, safety, legality, consent, permission, or authorization;
- autonomous operation in an offline competition environment;
- ARC-AGI-3 contest readiness, hidden-task generalization, or an official
  score;
- that retained state, feedback, pathfinding, or a passing ablation caused a
  performance improvement outside the measured comparison.

The reasoning kernel can route proposals, retain versioned evidence, run
bounded search and ablations, and produce verifiable receipts. The optional
control-owned execution coordinator can call a supplied single-writer executor
only after exact grant and lab checks; it does not supply an executor or create
authority. Strongwiz does not authenticate supplied evidence or replace a
domain's terminal authority. A domain adapter, executor, model artifact,
configuration, and external control plane are still required for an
operational system.

`execute_once` prevents ordinary in-process token replay and action/executor
substitution; it does not prove exactly-once effects across process, device, or
network failure. That stronger claim requires an executor-owned durable
idempotency and recovery protocol.

The executor command carries an idempotency key and post-call exceptions are
classified as `UNKNOWN_EFFECT`; neither fact supplies that external durable
protocol. Python also does not prevent hostile host code from retaining and
calling an executor reference outside the coordinator. The enforced claim is
the supported Strongwiz path, not an in-process security sandbox.

Evidence claims stay local to their acceptance surface:

- a unit test supports the tested software behavior;
- a frozen manifest identifies files and dependencies, not their correctness;
- call-boundary identity checks detect declared driver drift, not a malicious
  driver that lies about or transiently restores its identity;
- a hash chain detects changes relative to the recorded chain, not whether an
  observation was honest;
- a synthetic or public-domain result is not a private or official result;
- an observed domain success is one run, not a generalization result;
- a complete-looking intermediate state is not completion unless the domain
  authority reports success.

PEA Core v1.1.3, PECAN v1.0.4, and SEED v0.3 are bounded control profiles. They
help preserve distinctions and review records, but confer no ethical, legal,
institutional, or human authority.

Stronger claims require a frozen end-to-end runtime, a declared evaluation
class, preregistered denominators and budgets, complete failure evidence,
appropriate baselines and ablations, and the official evaluator when an
official claim is intended. Unresolved boundaries should remain `PARTIAL`,
`BLOCKED_EXTERNAL`, `FAILED_MECHANISM`, or `FAILED_INFRASTRUCTURE` rather than
being replaced by a smaller success.
