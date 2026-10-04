#!/usr/bin/env python3
"""Local app for the human blind-reading step (B0 / feasibility C1).

Usage:  python scripts/blind_reader_app.py <blind_sheet.html> <blind_answers.json> [--port 8765] [--no-browser]

Reads the cells of a blind sheet (<div class="c"><b>id</b> <i>hint</i><br><img src="data:..."> ...),
shows ONE cell at a time and autosaves after every cell to <blind_answers.json> in the same format the
sheet's own export uses:  {"b000": {"read": "29/9/7", "unsure": false}, ...}.
Resume: reopening the app continues from the first unanswered cell. Binds 127.0.0.1 only, stdlib only.
It never opens or serves the answer key; do not point it at one.
"""
import argparse
import html as htmllib
import json
import os
import re
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

PAGE = r"""<!doctype html><html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Đọc mù</title><style>
:root{--bg:#fff;--fg:#1a1a1a;--mut:#666;--ac:#2563eb;--card:#f4f5f7;--ok:#16a34a}
@media(prefers-color-scheme:dark){:root{--bg:#15171c;--fg:#e8e8e8;--mut:#9aa;--ac:#6ea0ff;--card:#1f222a}}
body{margin:0;font:16px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
main{max-width:900px;margin:0 auto;padding:16px}
.bar{height:8px;background:var(--card);border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:var(--ac)}
.card{background:var(--card);border-radius:10px;padding:16px;margin:12px 0}
img{max-width:100%;max-height:50vh;image-rendering:auto;border:1px solid #8886;border-radius:6px;background:#fff;transform-origin:left top}
input[type=text]{font-size:28px;width:100%;box-sizing:border-box;padding:8px 12px;border-radius:8px;border:2px solid var(--ac);background:var(--bg);color:var(--fg)}
button{font-size:15px;padding:8px 14px;border-radius:8px;border:1px solid #8886;background:var(--bg);color:var(--fg);cursor:pointer}
button.p{background:var(--ac);color:#fff;border-color:var(--ac)}small,.m{color:var(--mut)}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:10px}
</style></head><body><main>
<h2 style="margin:0">Đọc mù <span id="pos"></span></h2>
<p class="m" style="margin:4px 0 10px">Gõ <b>đúng giá trị nhìn thấy</b> trong ô, không đoán theo ngữ cảnh. Không chắc: bấm <b>?</b> (vẫn gõ phương án gần nhất). Tự lưu sau mỗi ô.</p>
<div class="bar"><i id="pb" style="width:0"></i></div>
<div class="card"><div><b id="cid"></b> <span class="m" id="hint"></span></div>
<div style="margin:10px 0"><img id="img" alt="ô cần đọc"></div>
<input id="val" type="text" autocomplete="off" spellcheck="false" placeholder="giá trị bạn đọc được…">
<div class="row"><label><input type="checkbox" id="uns"> không chắc / mơ hồ (phím <b>?</b>)</label>
<span style="flex:1"></span><button id="prev">← Trước</button><button id="next" class="p">Lưu &amp; tiếp (Enter)</button></div>
<div class="row"><button id="zin">Phóng to</button><button id="zout">Thu nhỏ</button><span class="m" id="st"></span></div></div>
<div id="done" class="card" style="display:none"><b style="color:var(--ok)">Đã đọc xong tất cả ô.</b> Kết quả đã lưu vào file. Bạn có thể đóng trang này và trả lời <code>done</code> cho agent.</div>
</main><script>
let cells=[],ans={},i=0,zoom=1;const $=id=>document.getElementById(id);
async function save(){const r=await fetch('/answers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(ans)});$('st').textContent=r.ok?'đã lưu '+new Date().toLocaleTimeString():'LỖI LƯU';}
function stash(){const c=cells[i];ans[c.id]={read:$('val').value.trim(),unsure:$('uns').checked};}
function show(){const c=cells[i];$('cid').textContent=c.id;$('hint').textContent=c.hint;$('img').src=c.img;$('img').style.transform='scale('+zoom+')';
const a=ans[c.id]||{read:'',unsure:false};$('val').value=a.read;$('uns').checked=a.unsure;
const n=cells.filter(c=>ans[c.id]&&ans[c.id].read!=='').length;$('pos').textContent='— ô '+(i+1)+'/'+cells.length+' (đã đọc '+n+')';$('pb').style.width=(100*n/cells.length)+'%';$('val').focus();$('val').select();}
async function go(d){stash();await save();const n=i+d;if(n<0)return;if(n>=cells.length){const left=cells.filter(c=>!ans[c.id]||ans[c.id].read==='');if(left.length){i=cells.indexOf(left[0]);alert('Còn '+left.length+' ô chưa gõ; chuyển tới ô đầu tiên chưa đọc.');show();return}
$('done').style.display='block';return}i=n;show();}
$('next').onclick=()=>go(1);$('prev').onclick=()=>go(-1);
$('zin').onclick=()=>{zoom=Math.min(3,zoom+.25);show()};$('zout').onclick=()=>{zoom=Math.max(.5,zoom-.25);show()};
document.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();go(1)}else if(e.key==='?'){e.preventDefault();$('uns').checked=!$('uns').checked}else if(e.key==='ArrowLeft'&&e.ctrlKey){go(-1)}});
(async()=>{cells=await (await fetch('/cells')).json();ans=await (await fetch('/answers')).json();
const first=cells.findIndex(c=>!ans[c.id]||ans[c.id].read==='');i=first<0?0:first;show();})();
</script></body></html>"""


def parse_cells(path):
    s = open(path, encoding="utf-8").read()
    out = []
    for block in re.findall(r'<div class="c">(.*?)</div>', s, flags=re.S):
        m_id = re.search(r"<b>(.*?)</b>", block, flags=re.S)
        m_hint = re.search(r"<i>(.*?)</i>", block, flags=re.S)
        m_img = re.search(r'<img[^>]*src="([^"]+)"', block)
        if m_id and m_img:
            out.append({"id": htmllib.unescape(m_id.group(1)).strip(),
                        "hint": htmllib.unescape(m_hint.group(1)).strip() if m_hint else "", "img": m_img.group(1)})
    if not out:
        sys.exit("no cells found: is this a blind sheet (<div class=\"c\"><b>id</b> ...<img src=...>)?")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sheet")
    ap.add_argument("answers")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if "key" in os.path.basename(a.sheet).lower():
        sys.exit("refusing to open a file that looks like an answer key")
    cells = parse_cells(a.sheet)
    cells_json = json.dumps(cells, ensure_ascii=False).encode("utf-8")
    lock = threading.Lock()

    def load():
        try:
            return json.load(open(a.answers, encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path == "/cells":
                self._send(200, cells_json, "application/json")
            elif self.path == "/answers":
                with lock:
                    self._send(200, json.dumps(load(), ensure_ascii=False).encode("utf-8"), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/answers":
                return self._send(404, b"not found", "text/plain")
            try:
                data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                ids = {c["id"] for c in cells}
                clean = {k: {"read": str(v.get("read", "")), "unsure": bool(v.get("unsure", False))}
                         for k, v in data.items() if k in ids}
            except (ValueError, AttributeError):
                return self._send(400, b"bad json", "text/plain")
            with lock:
                tmp = a.answers + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(clean, f, ensure_ascii=False, indent=1)
                os.replace(tmp, a.answers)
            self._send(200, b"ok", "text/plain")

    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"{len(cells)} ô. Mở {url}  (Ctrl+C để dừng). Lưu vào: {os.path.abspath(a.answers)}")
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
