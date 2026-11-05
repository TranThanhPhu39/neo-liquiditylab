# Third-party runtime

- Pyodide 0.27.7: https://github.com/pyodide/pyodide/tree/0.27.7
  Runtime distributed under the Mozilla Public License 2.0, including its upstream component notices.
  See `runtime/PYODIDE-LICENSE` and `runtime/pyodide.asm.js`, plus the Python standard-library archive's notices.
- CPython: Python Software Foundation License; https://docs.python.org/3/license.html
- Rubik: SIL Open Font License 1.1. See `fonts/OFL.txt`.
- Plotly.js gl3d 3.7.0: MIT, https://github.com/plotly/plotly.js/tree/v3.7.0.
  Vendored from the official Plotly CDN; see `vendor/PLOTLY-LICENSE` and `vendor/plotly-manifest.json`.

The runtime is vendored; user-imported market data is not sent to any runtime CDN.
