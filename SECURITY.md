# Security policy

Strongwiz is pre-alpha research software. It has not been independently audited
and is not a security, safety, policy, or authorization product.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting or a private security
advisory for `Grativy6/strongwiz`. Do not publish credentials, private traces,
sealed evaluation material, or a working exploit in a public issue.

Include, when available:

- the affected commit and platform;
- a minimal reproduction using synthetic data;
- the expected and observed guard or receipt behavior;
- whether integrity, authorization, confidentiality, availability, or replay
  determinism is affected; and
- any known workaround that does not erase evidence.

No response or remediation deadline is promised. A report is evidence to
investigate, not permission to access systems or data beyond what you already
have authorization to test.

## Security-relevant invariants

- Model output, tool output, documents, and adapters are untrusted inputs.
- Authorization must come from an external grant and be revalidated before a
  consequential release or action.
- A receipt proves only its recorded content and chain position; it does not
  prove that an upstream observation was truthful.
- Hash integrity is not identity, consent, correctness, confidentiality, or
  non-repudiation.
- Caches and derived facts must be version-bound and invalidated when an
  implicated dependency changes.
- Drivers and domain adapters must not receive credentials they do not need.
- Production integrations should use least privilege, explicit budgets,
  timeouts, bounded retries, and a human-accessible stop.

## Supported versions

Only the latest commit on the default branch is considered for security fixes
during pre-alpha development. Historical experimental commits are retained for
provenance but are not supported releases.
