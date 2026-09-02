# Calibration 002 public receipt set

This directory contains 31 exact byte-for-byte copies of concise run,
transition, and handoff artifacts, plus one derived campaign summary. Their
sizes and SHA-256 identities are listed in `artifact-index.json`.

The set supports public inspection of the declared result and the adaptive
transfer boundary. It is not the complete evidence capsule. Raw frames, raw
traces, official recordings, SQLite ledgers, complete capsules, game source,
and capabilities remain local and ignored. Their content identities are bound
by the published metadata, but the omitted material cannot be independently
replayed or inspected from Git alone.

No account, owner credential, competition entry, or submission was used. The
campaign result was `PARTIAL`, the final official state was `NOT_FINISHED`, the
best observed progress was 1 of 7 levels, and
`completion_genuinely_observed=false`. Stage 4 ended at the owner's resource
pause.

To verify the tracked exact copies on PowerShell:

```powershell
$root = 'docs/calibrations/receipts/002'
$index = Get-Content "$root/artifact-index.json" -Raw | ConvertFrom-Json
$index.tracked_exact_copies | ForEach-Object {
  $file = Get-Item -LiteralPath "$root/$($_.path)"
  $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
  [pscustomobject]@{
    Path = $_.path
    SizeMatches = $file.Length -eq $_.size_bytes
    HashMatches = $hash -eq $_.sha256
  }
}
```

Full capsule closure can be checked only where the retained local capsules are
available. The four exact capsule manifests enumerate those closures.
