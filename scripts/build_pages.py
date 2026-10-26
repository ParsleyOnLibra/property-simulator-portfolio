"""Build the browser-only GitHub Pages demo from the v0.7 interface."""

from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "property_simulator_v07"
TARGET = ROOT / "docs"

def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"Expected one occurrence of {old[:60]!r}")
    return text.replace(old, new, 1)

def pattern(text, search, replacement):
    result, count = re.subn(search, replacement, text, count=1, flags=re.DOTALL)
    if count != 1:
        raise ValueError(f"Expected one match for {search!r}")
    return result

html = (SOURCE / "static" / "index.html").read_text(encoding="utf-8")
html = pattern(html, r"<title>.*?</title>", '<title>Property x Equity | Public demo</title><meta name="description" content="Convert a property price into shares using sample or your own CSV data.">')
html = once(html, "</style></head>", '</style><link rel="stylesheet" href="static.css"></head>')
html = pattern(html, r"<header>.*?</header>", '<header><nav><div class="brand">PROPERTY / EQUITY</div><span class="tag">PUBLIC DEMO</span></nav></header>')
html = once(html, '<button id="tabSync">', '<button id="tabSync" hidden>')
html = once(html, '<section id="syncPanel" class="card">', '<section id="syncPanel" class="card" hidden>')
html = pattern(html, r'<section class="card"><h2>.*?</h2><div id="housingStatus"', '<section class="card" id="housingPanel" hidden><h2>Housing</h2><div id="housingStatus"')
html = once(html, '<section class="card" id="rankPanel">', '<section class="card" id="rankPanel" hidden>')
html = pattern(html, r'(<section id="importPanel".*?<p class="muted">).*?(</p>)', r'\1Files are read in your browser and are not uploaded. Refreshing restores the synthetic sample data. Save each file as UTF-8 CSV.\2')
html = pattern(html, r'(<div id="stockChartPanel".*?<p class="caption muted">).*?(</p></div><div id="results")', r'\1The chart shows imported prices only. A horizontal line marks your input price; it does not prove a trade happened at that price.\2')
html = pattern(html, r'<footer>.*?</footer>', '<footer>Browser-only public demo. Sample prices and exchange rates are synthetic, not market quotes. CSV files stay in your browser. <a href="https://github.com/ParsleyOnLibra/property-simulator-portfolio">Source code</a></footer>')
html = once(html, "<script>\n'use strict';", '<script src="static-api.js"></script>\n<script>\n\'use strict\';')
lines = html.splitlines(keepends=True)
matches = [i for i, line in enumerate(lines) if line.startswith("async function api(path,body)")]
if len(matches) != 1:
    raise ValueError("Could not locate API adapter")
lines[matches[0]] = "async function api(path,body){return window.staticApi(path,body);}\n"
html = "".join(lines)
start = html.index("(async()=>{try{const state=await api('/api/sync/status')")
end = html.index("})();", start) + len("})();")
html = html[:start] + """(async()=>{try{
  await load(await api('/api/demo'),true);
  $('houseDate').value=catalog.ranges.apartments.end;
  $('stockDate').value=catalog.ranges.stocks.end;
  $('priceMode').value='DATE';updatePriceMode();
  await calculate();
}catch(e){error(e);$('results').innerHTML='<div class="card empty">Could not load sample data. Refresh this page.</div>';}})();""" + html[end:]

TARGET.mkdir(exist_ok=True)
(TARGET / "index.html").write_text(html, encoding="utf-8", newline="\n")
(TARGET / ".nojekyll").write_text("", encoding="utf-8")
(TARGET / "data").mkdir(exist_ok=True)
for name in ("apartments.csv", "stocks.csv", "fx.csv"):
    shutil.copyfile(SOURCE / "data" / name, TARGET / "data" / name)
print("Built GitHub Pages demo in docs/")
