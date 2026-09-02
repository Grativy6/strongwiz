# Calibration 001 public receipt set

This directory contains exact byte-for-byte copies of the two concise terminal
records, run seals, evidence-capsule manifests, and external delivery receipts.
Their identities are listed in `artifact-index.json`.

The set supports public inspection of the declared result and its content
identities. It is not the complete evidence capsule. Raw frames, raw traces,
official recordings, SQLite ledgers, opaque game state, and the multi-gigabyte
capsule remain local and ignored; their sizes and hashes are retained through
the exact terminal records and seals. The game source is not included.

No account, owner credential, competition entry, or submission was used. The
observed terminal result was `PARTIAL`, `NOT_FINISHED`, 4/7 levels at best, and
`completion_genuinely_observed=false`.

To check the tracked copies on PowerShell:

```powershell
Get-ChildItem docs/calibrations/receipts/001 -File |
  Where-Object Name -NotIn README.md,artifact-index.json,postrun-finalization.receipt.json |
  Get-FileHash -Algorithm SHA256
```

Full capsule closure can be checked only where the retained local capsule is
available:

```powershell
.venv/Scripts/python.exe -c "from scripts.strongwiz_streaming_postrun import verify_evidence_capsule_streaming as verify; print(verify(r'artifacts/local/calibration-001/attempt-002-capsule', expected_capsule_ref='803a01fd841271e31983326380e65592a0f5235e5ba681670a522c33ad8814b7').digest)"
```
