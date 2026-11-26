"""Pad probe: how does this browser number a game controller's buttons and axes? (stdlib only)

Usage:  python python/pad_probe.py [port]          then open http://127.0.0.1:8765/ in the browser to test

The page asks for each button in turn (B, A, Y, X by place on a SNES pad, then L, R, Select, Start, the D-pad).
For each one it records which raw button index went down, or which axis moved and to what. Every step is
appended to python/pad_probe_log.jsonl (one JSON object per line) and printed here, with the pad's id and
mapping ("standard" or ""). That's what a remap table for an odd adapter needs.
"""
import http.server
import json
import pathlib
import sys

LOG = pathlib.Path(__file__).with_name("pad_probe_log.jsonl")   # what each browser reported, kept between runs

PAGE = """<!doctype html><meta charset="utf-8"><title>Pad probe</title>
<style>body{font:20px system-ui;background:#160d1f;color:#ffd6f0;margin:2em}b{color:#ff7ac8}#raw{font:14px monospace;color:#c9a}</style>
<h1>Pad probe</h1><p id="who">Press any button on the controller...</p><p>Now press: <b id="ask">-</b></p>
<p id="done"></p><pre id="raw"></pre>
<script>
const STEPS = ["B (bottom)", "A (right)", "Y (left)", "X (top)", "L", "R", "Select / Minus", "Start / Plus",
  "D-pad UP", "D-pad DOWN", "D-pad LEFT", "D-pad RIGHT", "Home (if it has one)"];
let step = 0, rest = null, busy = false;
const pad = () => Array.from(navigator.getGamepads()).find(p => p);   // the first controller the browser sees
const snap = p => ({ buttons: p.buttons.map(b => +(b.pressed || b.value > 0.5)), axes: p.axes.map(a => +a.toFixed(3)) });
function changed(a, b) {                                               // what differs from the resting state
  const out = { buttons: [], axes: {} };
  a.buttons.forEach((v, i) => { if (v && !b.buttons[i]) out.buttons.push(i); });
  a.axes.forEach((v, i) => { if (Math.abs(v - b.axes[i]) > 0.4) out.axes[i] = v; });
  return out;
}
function tick() {
  const p = pad();
  if (p) {
    const now = snap(p);
    document.getElementById("who").textContent = "id: " + p.id + "  |  mapping: \\"" + p.mapping + "\\"";
    document.getElementById("raw").textContent = "buttons " + now.buttons.join("") + "\\naxes " + now.axes.join("  ");
    if (!rest) rest = now;                                             // the first look is "nothing held"
    const d = changed(now, rest), any = d.buttons.length || Object.keys(d.axes).length;
    if (step < STEPS.length) {
      document.getElementById("ask").textContent = STEPS[step];
      if (any && !busy) {                                              // a press: record it, then wait for let-go
        busy = true;
        fetch("/log", { method: "POST", body: JSON.stringify({ id: p.id, mapping: p.mapping, ua: navigator.userAgent,
          step: STEPS[step], pressed: d.buttons, axes: d.axes, rest: rest.axes }) });
        step++;
      } else if (!any) busy = false;
    } else {
      document.getElementById("ask").textContent = "all done, thank you!";
      document.getElementById("done").textContent = "You can close this tab.";
    }
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
</script>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                           # the probe page
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):                                          # one step's result: log it and print it
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")
        print(f"{data.get('step')}: buttons {data.get('pressed')} axes {data.get('axes')}  [{data.get('mapping')!r} {data.get('id')}]", flush=True)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):                               # quiet: only the steps above
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"Pad probe on http://127.0.0.1:{port}/  (log: {LOG})", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
