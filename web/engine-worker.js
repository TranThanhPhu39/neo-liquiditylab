/* Run the repository's Python/Decimal engine off the UI thread. */
let engine;
async function initialize() {
  importScripts('./runtime/pyodide.js');
  engine = await loadPyodide({indexURL: new URL('./runtime/', self.location.href).href});
  const response = await fetch('./engine.zip?v=20261105-svg1');
  if (!response.ok) throw new Error('Không tải được bộ tính.');
  engine.unpackArchive(await response.arrayBuffer(), 'zip', {extractDir: '/app'});
  engine.runPython("import sys, zoneinfo\nsys.path.insert(0, '/app')\nzoneinfo.reset_tzpath(['/app/zones'])\nfrom src.app.browser_bridge import ingest, view, portfolio_symbols");
}
const ready = initialize();
let queue = Promise.resolve();
self.onmessage = ({data}) => {
  queue = queue.then(async () => {
    try {
      await ready;
      const fn = engine.globals.get(data.method);
      try { self.postMessage({id: data.id, result: JSON.parse(fn(data.text))}); }
      finally { fn.destroy(); }
    } catch (error) {
      self.postMessage({id: data.id, error: String(error.message).split('\n').filter(Boolean).slice(-1)[0]});
    }
  });
};
ready.then(() => self.postMessage({ready: true})).catch(error => self.postMessage({fatal: String(error.message)}));
