# Facebook Comment Tools

## Structure

- `scripts/`: runnable scripts.
- `data/raw/`: source JSON files.
- `data/processed/`: generated CSV files and enriched outputs.
- `backups/`: backup files and checkpoints.
- `backups/deprecated_scripts/`: old scripts kept only for reference, not used in the current workflow.
- `debug/`: one-off debugging scripts.
- `profiles/`: browser profiles used by Playwright login sessions.

## Common Commands

Login Facebook in the Playwright browser:

```bash
python3 scripts/enrich_commenter_name_browser.py --login
```

Enrich a small sample:

```bash
python3 scripts/enrich_commenter_name_browser.py --limit 5
```

Enrich all rows:

```bash
python3 scripts/enrich_commenter_name_browser.py
```

Convert `data/raw/comments.json` to `data/processed/comments.csv`:

```bash
node scripts/export-comments-csv.js
```
