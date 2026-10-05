"""Drive the web app in headless Chrome over the DevTools protocol (stdlib only), against a server with a fake model.

Opt-in by environment: the whole module skips when Google Chrome is not installed."""
import base64
import json
import os
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request

import pytest

from decision_tune.server import make_server
from test_server import Fake

CHROME = os.environ.get("CHROME_BIN", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
if not os.path.exists(CHROME):
    pytest.skip("Google Chrome not found (set CHROME_BIN)", allow_module_level=True)

INIT_JS = """
window.__errs=[]; addEventListener('error',x=>__errs.push(String(x.message))); addEventListener('unhandledrejection',x=>__errs.push('rej '+x.reason));
document.addEventListener('securitypolicyviolation',x=>__errs.push('CSP '+x.violatedDirective+' '+x.blockedURI));
window.__delay=0; window.__runs=[];
const f=window.fetch; window.fetch=async (p,o)=>{ if(String(p).includes('/api/preview')&&window.__delay) await new Promise(r=>setTimeout(r,window.__delay)); if(String(p).includes('/api/run')) window.__runs.push(JSON.parse(o.body).input); return f(p,o); };"""


class WS:
    """Minimal client-side websocket (text frames only) speaking CDP."""

    def __init__(self, url):
        host, rest = url[5:].split("/", 1)
        h, p = host.split(":")
        self.s = socket.create_connection((h, int(p)), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(f"GET /{rest} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.s.recv(1)
        self.n = 0

    def send(self, obj):
        d = json.dumps(obj).encode()
        hdr = b"\x81"
        if len(d) < 126:
            hdr += bytes([0x80 | len(d)])
        elif len(d) < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", len(d))
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", len(d))
        mask = os.urandom(4)
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(d)))

    def _read(self, n):
        b = b""
        while len(b) < n:
            c = self.s.recv(n - len(b))
            if not c:
                raise EOFError
            b += c
        return b

    def recv(self):
        data = b""
        while True:
            b1, b2 = self._read(2)
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            data += self._read(ln)
            if b1 & 0x80:
                return json.loads(data)

    def call(self, method, **params):
        self.n += 1
        self.send({"id": self.n, "method": method, "params": params})
        while True:
            m = self.recv()
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m["result"]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Page:
    def __init__(self, ws, base):
        self.ws, self.base = ws, base

    def ev(self, js):
        r = self.ws.call("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

    def wait(self, js, timeout=30):
        end = time.time() + timeout
        while time.time() < end:
            if self.ev(js):
                return
            time.sleep(0.2)
        raise AssertionError("timeout waiting for: " + js)

    def goto(self, width=1200):
        self.ws.call("Emulation.setDeviceMetricsOverride", width=width, height=900 if width > 600 else 800, deviceScaleFactor=1, mobile=width < 600)
        self.ws.call("Page.navigate", url=self.base + "/")
        self.wait("document.readyState==='complete' && !!document.querySelector('#status .on')")

    def set_paste(self, v):
        self.ev(f"(()=>{{const p=document.getElementById('paste'); p.value={json.dumps(v)}; p.dispatchEvent(new Event('input'));}})()")

    def save_recipe(self, text):
        return self.ev(f"fetch('/api/recipes',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:{json.dumps(text)}}}).then(r=>r.status)")

    def errors(self):
        return self.ev("JSON.stringify(window.__errs)")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    home = tmp_path_factory.mktemp("home")
    os.environ["DECISION_TUNE_HOME"] = str(home)
    srv = make_server(Fake(), port=0)
    threading.Thread(target=srv.serve_forever, args=(0.01,), daemon=True).start()
    port = _free_port()
    proc = None
    prof = tempfile.mkdtemp(prefix="dt-chrome-")
    try:
        proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", f"--remote-debugging-port={port}", f"--user-data-dir={prof}", "about:blank"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = None
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                ws = WS(next(t for t in tabs if t["type"] == "page")["webSocketDebuggerUrl"])
                break
            except Exception:
                time.sleep(0.3)
        assert ws, "chrome did not start"
        ws.call("Page.enable")
        ws.call("Runtime.enable")
        ws.call("Page.addScriptToEvaluateOnNewDocument", source=INIT_JS)
        yield Page(ws, f"http://127.0.0.1:{srv.server_address[1]}")
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()
        srv.shutdown()
        srv.server_close()
        os.environ.pop("DECISION_TUNE_HOME", None)


@pytest.mark.parametrize("width", [1200, 390])
def test_try_sample_run_export(page, width):
    p = page
    p.goto(width)
    p.ev("document.getElementById('decide').click()")
    p.wait("document.getElementById('win').textContent.length>0")
    p.ev("document.getElementById('t-run').click()")
    p.ev("document.getElementById('sample').click()")
    p.wait("document.querySelectorAll('#qs .q').length==3")
    p.ev("document.getElementById('runbtn').click()")
    p.wait("!document.getElementById('results').hidden")
    total = p.ev("document.querySelectorAll('#tbody tr').length")
    assert total > 0
    assert "% sure" in p.ev("document.querySelector('#tbody tr').innerText")
    assert p.ev("(document.getElementById('f-review').click(), document.querySelectorAll('#tbody tr').length)") <= total
    # spreadsheet formula injection is neutralized on export
    out = p.ev("fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({columns:['a'],rows:[{a:'=1+1'}],format:'csv'})}).then(r=>r.text())")
    assert "'=1+1" in out, out
    for tab in ("t-connect", "t-run"):
        p.ev(f"document.getElementById('{tab}').click()")
        assert p.ev("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), f"horizontal overflow on {tab} at {width}px"
    assert p.errors() == "[]"


def test_save_and_list_recipe(page):
    p = page
    p.goto()
    p.ev("document.getElementById('t-run').click()")
    p.ev("document.getElementById('sample').click()")
    p.wait("document.querySelectorAll('#qs .q').length==3")
    p.ev("document.getElementById('savebtn').click()")
    p.ev("document.getElementById('rname').value='ui-test'; document.getElementById('rname').dispatchEvent(new Event('input'))")
    p.ev("document.getElementById('savego').click()")
    p.wait("document.getElementById('runmsg').textContent.startsWith('Saved')")
    p.ev("document.getElementById('t-recipes').click()")
    p.wait("[...document.querySelectorAll('#reclist h3')].some(h=>h.textContent.includes('ui-test'))")
    assert p.errors() == "[]"


def test_run_waits_for_preview_and_clear_cancels(page):
    p = page
    p.goto()
    p.ev("document.getElementById('t-run').click()")
    p.set_paste("old1\nold2")
    p.wait("src && src.input.data.startsWith('old1')")
    # 13a: Run must not submit the previous dataset while a replacement is being read
    p.ev("window.__delay=1500; window.__runs=[]")
    p.set_paste("new1\nnew2\nnew3")
    assert p.ev("document.getElementById('runbtn').disabled"), "Run must be disabled during the debounce window"
    time.sleep(0.6)
    p.ev("document.getElementById('runbtn').click()")
    time.sleep(0.2)
    assert p.ev("window.__runs.length") == 0, p.ev("JSON.stringify(window.__runs)")
    p.wait("!document.getElementById('runbtn').disabled && src.input.data.startsWith('new1')")
    # 13b: clearing the box cancels a pending preview
    p.ev("window.__delay=1500")
    p.set_paste("later1\nlater2")
    time.sleep(0.6)
    p.set_paste("")
    time.sleep(2.4)  # the late response has arrived by now
    assert p.ev("src === null && document.getElementById('runbtn').disabled && document.getElementById('cols').textContent.includes('Add your items first')")
    p.ev("window.__delay=0")


def test_recipe_load_semantics(page):
    p = page
    p.goto()
    p.ev("document.getElementById('t-run').click()")
    p.save_recipe(json.dumps({"name": "allcols", "read": [], "review_below": 0.605, "questions": [{"name": "q", "type": "yes_no", "question": "Is it?"}]}))
    p.save_recipe('{"name":"protos","read":[],"questions":[{"name":"q","type":"choose","question":"Q?","options":{"__proto__":"P: proto","b":"B: two"}},{"name":"l","type":"choose","question":"L?","options":["Billing: charges","Shipping: delivery"]}]}')
    p.ev("setSource({type:'csv',data:'id,error,note\\n1,keep me,refund hello\\n2,,bye\\n'}, 't.csv')")
    p.wait("document.querySelectorAll('#cols input').length==3")
    p.ev("loadRecipe('allcols')")
    saved = "document.getElementById('usenote').textContent.startsWith('The saved recipe')"
    unsaved = "document.getElementById('usenote').textContent.startsWith('Press Save')"
    p.wait(saved)

    # 14: empty read ticks every column; the threshold keeps its decimals
    assert p.ev("checkedCols().join(',')") == "id,error,note"
    assert p.ev("document.getElementById('thr').value") == "60.5"
    assert p.ev("buildRecipe(true).review_below") == 0.605

    # 15: editing a loaded recipe makes it 'not saved'
    p.ev("(()=>{const t=document.querySelector('#qs .q .qt'); t.value='Is it, really?'; t.dispatchEvent(new Event('input',{bubbles:true}));})()")
    assert p.ev(unsaved), "editing a question must clear 'saved'"
    for edit in ("(()=>{const c=document.querySelector('#cols input'); c.checked=false; c.dispatchEvent(new Event('change',{bubbles:true}));})()",
                 "(()=>{const t=document.getElementById('thr'); t.value='70'; t.dispatchEvent(new Event('input',{bubbles:true}));})()",
                 "document.querySelector('#qs .q button').click()"):
        p.ev("loadRecipe('allcols')")
        p.wait(saved)
        p.ev(edit)
        assert p.ev(unsaved), edit

    # 17: a source column named error is data, not a failure
    p.ev("loadRecipe('allcols')")
    p.wait(saved)
    p.ev("document.getElementById('runbtn').click()")
    p.wait("!document.getElementById('results').hidden")
    row = p.ev("document.querySelector('#tbody tr').innerText.replace(/\\n/g,' ')")
    assert "% sure" in row and "Error" not in row, row
    assert "2 rows · 2 decisions" in p.ev("document.getElementById('sumtxt').textContent")

    # 16: option keys survive loading
    p.ev("loadRecipe('protos')")
    p.wait("questions().length==2")
    qs = json.loads(p.ev("JSON.stringify(questions())"))
    assert qs[1]["options"] == ["Billing: charges", "Shipping: delivery"]
    assert p.ev("Object.keys(questions()[0].options).join(',')") == "__proto__,b"
    assert '"__proto__"' in p.ev("JSON.stringify(questions()[0].options)")
    p.ev("""last = {out:{rows:[{id:1,q:'toString',l:'Billing: charges'}],columns:[],needs_review:0,count:1,failed:[],ms:1}, recipe:{name:'x',read:['id'],review_below:0.6,
         questions:[{name:'q',type:'choose',question:'?',options:['toString','valueOf']},{name:'l',type:'choose',question:'?',options:['Billing: charges','Shipping: delivery']}]}}; for (const k of ['q_confidence','l_confidence']) last.out.rows[0][k]=0.9; renderResults();""")
    cells = p.ev("[...document.querySelectorAll('#tbody tr td .v')].map(x=>x.textContent).join('|')")
    assert cells == "toString|Billing: charges", cells
    assert p.errors() == "[]"
