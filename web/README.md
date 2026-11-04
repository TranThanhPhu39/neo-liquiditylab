# LiquidityLab dashboard

Public web: https://tranthanhphu39.github.io/neo-liquiditylab/

Three screens: liquidity, execution A1 and portfolio stress B5. The Penpot monochrome design uses the same repository Python/Decimal finance core, running in a vendored Pyodide Web Worker.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m scripts.build_web_dashboard
.\.venv\Scripts\python.exe -m http.server 8876 --directory web
```

Open `http://localhost:8876`. GitHub Pages also serves this directory under `/neo-liquiditylab/`; asset and worker URLs are relative to support that base path.

For real data, export from the local normalized archive:

```powershell
.\.venv\Scripts\python.exe -m scripts.export_dashboard
```

Then select the JSON using **Mở dữ liệu**. Imported files remain in browser tab memory. No DNSE keys, market archives, database files, or Sites credentials are included in this web artifact. Automatic data synchronization is not implemented yet.

Edit HTML/CSS/JS here and Python finance modules in `src/`. Push to `main`; `.github/workflows/dashboard-pages.yml` rebuilds the Python bundle and publishes this directory. The Pages workflow needs no API secrets and does not alter collector schedules.

Pyodide and Rubik license notices are in `THIRD_PARTY.md`, `runtime/PYODIDE-LICENSE`, and `fonts/OFL.txt`.
