# LiquidityLab dashboard

Public web: https://tranthanhphu39.github.io/neo-liquiditylab/

Three screens: liquidity, execution A1 and portfolio stress B5. Liquidity and the interactive 3D depth surface render directly in JavaScript/SVG without waiting for Python or WebGL. A1/B5 use the same repository Python/Decimal finance core, loaded lazily in a vendored Pyodide Web Worker with startup/request deadlines. Imported financial eligibility is rechecked by Python.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m scripts.build_web_dashboard
.\.venv\Scripts\python.exe -m http.server 8876 --directory web
```

Open `http://localhost:8876`. The latest published DNSE date loads automatically. GitHub Pages also serves this directory under `/neo-liquiditylab/`; asset and worker URLs are relative to support that base path.

To refresh the public historical partitions from an audited local REST archive:

```powershell
.\.venv\Scripts\python.exe -m scripts.export_web_data --publish
```

This publishes read-only, normalized market-data samples under `data/`. Market partitions contain last-source snapshots at 11:29, 14:20, and 15:10. Hash-sharded history partitions contain the last source event in each six-minute bucket. The browser loads history only for the selected symbol and portfolio symbols, verifies SHA-256, preserves unit checks and NO DATA, and never uses future snapshots. This is sampled ex-post history, not a tick-complete or realtime feed.

After a new daily collection, one command can audit, normalize and publish the web data:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_dnse_day --publish-web
```

REST collection/normalization still runs on the local machine. The GitHub WebSocket collector saves raw encrypted checkpoints; it is not automatically normalized into these public REST partitions. No DNSE keys, raw market archives, full SQLite database, or Sites credentials are included. **Mở tệp riêng** remains optional; its contents stay in the browser tab and are not uploaded.

Edit HTML/CSS/JS here and Python finance modules in `src/`. Push to `main`; `.github/workflows/dashboard-pages.yml` rebuilds the Python bundle and publishes this directory. The Pages workflow needs no API secrets and does not alter collector schedules.

Pyodide and Rubik license notices are in `THIRD_PARTY.md`, `runtime/PYODIDE-LICENSE`, and `fonts/OFL.txt`.
