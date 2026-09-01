# Reasoning loop

Strongwiz organizes work around one governing goal and as many bounded
subgoals as the current uncertainty requires. A distinction is meaningful only
when competing resolutions predict different consequences and could change a
plan, risk judgment, candidate choice, experiment, resource use, access,
hazard response, movement, progress, or output.

The runtime loop is:

1. **Observe.** A domain adapter converts raw state into an immutable
   observation while retaining a reference to the raw evidence.
2. **Scan.** Bind the observation to the governing goal, current scoped goal,
   active distinctions, retained facts, and optional continuation trace.
3. **Choose depth.** Work in the fast lane while a supported plan remains
   credible. Switch to deep reasoning for startup uncertainty, structural
   novelty, a meaningful contradiction, reopening, an invalid plan, high goal
   uncertainty, repeated lack of progress, or a bounded fast-streak limit.
4. **Propose.** Model drivers return one or more actions with competing
   predictions, falsifiers, alternatives, declared costs, and a concise
   decision-relevant rationale. They do not return grants or control state.
5. **Route.** The control-owned router checks current identity, witnesses,
   scope, trace, external authority, allowed consequences, resources, and
   re-entry evidence. Failed hard guards reject; missing evidence requests a
   witness; unresolved authority or resources hold. Eligible proposals are
   ordered deterministically without combining unlike costs into a fictional
   universal utility.
6. **Admit under the lab rules.** PEA, PECAN, and SEED remain independently
   supplied control records. The coordinator rechecks that the selected route
   belongs to the exact control snapshot and that grant, task, goal, proposal,
   action, observation ID/digest, goal ID/digest, scope, executor, attention
   budget, and destination identities agree.
   The task grant independently classifies whether a human-facing release is in
   scope; that classification or a declared `OUTPUT` effect makes SEED review
   mandatory.
7. **Act externally.** If an effect is authorized, a separate single writer
   is called through the coordinator, which consumes a one-use permit and never
   exposes its token. It receives the exact invocation and idempotency identity.
   The active grant is checked before work and immediately
   before the call; replacement, revocation, expiry, or scope drift quarantines
   the attempt. Release and execution outcome remain distinct receipts.
8. **Assess.** Require the exact completed release, attempt, route, action, and
   executor-evidence tuple from the coordinator-issued handoff, and require its
   route/control pair to equal the pending decision. Then compare predicted and
   observed consequence channels. Preserve matching components, localize
   unexpected or unobserved consequences, and
   revise exactly the implicated components first. Repeated local failure may
   widen to a dependency or scoped model; it does not silently rewrite the
   entire history.
9. **Continue or stop.** Retain earned mechanics across surfaces. Treat a new
   surface as an attentional closure of the old one, not deletion, and reopen
   the smallest relevant goal or model when later evidence requires it. Only
   the domain adapter supplies success, failure, blocked, or continue status.
10. **Receipt.** Append scan, decision, assessment, outcome, and terminal records
   to the evidence ledger. Preserve failed, invalid, and unattempted work under
   the denominator declared before an experiment.

With a ledger configured, each actionable transition is durably appended
before in-memory state advances. A storage failure therefore leaves the prior
phase in force.

When uncertainty blocks progress, investigation favors the smallest reversible
action that distinguishes consequential alternatives. When a credible plan is
supported, execution favors the shortest declared progress path. The purpose
of learning is better decisions toward the governing goal, not exhaustive
enumeration of every possible interaction.
