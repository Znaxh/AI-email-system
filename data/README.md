# Company data directory (local only)

Upload your policy document and transactions through **Settings → Company data**.
Runtime serves the active versioned bundle under `company/` in BlobStore — not
hardcoded files from this folder.

Tracked sample files live under **`tests/fixtures/northpeak/`**
(`policy.pdf`, `transactions.json`, `dataset.json`). This `data/` directory is
intentionally empty in git.

## Optional one-time legacy bootstrap

If `company/active.json` is missing **and** this folder contains:

- `policy.*` (pdf / docx / md / txt)
- `transactions.json` (or another tabular export the importer can stage)

…the app imports them once on first boot. You can seed that by copying from the
fixtures:

```bash
cp tests/fixtures/northpeak/policy.pdf data/
cp tests/fixtures/northpeak/transactions.json data/
cp tests/fixtures/northpeak/dataset.json data/   # optional tickets
```

`data/policy.*`, `data/transactions.json`, `data/dataset.json`, and
`data/user_examples.json` are gitignored so local copies never get committed.
