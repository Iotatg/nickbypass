"""Web UI：uvicorn linkresolver.server:app --port 8000"""

from __future__ import annotations

import time

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .engine import Resolver, normalize

app = FastAPI(title="Link Resolver", docs_url=None, redoc_url=None)


class Req(BaseModel):
    url: str
    browser: bool = True
    timeout: float = 25.0


@app.post("/api/resolve")
async def api_resolve(req: Req):
    if not normalize(req.url):
        return JSONResponse({"ok": False, "note": "请输入链接", "steps": [], "final": ""}, status_code=400)

    def work():
        return Resolver(timeout=req.timeout, allow_browser=req.browser).resolve(req.url).to_dict()

    data = await run_in_threadpool(work)
    return JSONResponse(data)


@app.get("/health")
async def health():
    return {"ok": True, "ts": time.time()}


@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE


PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Link Resolver · 短链 / 广告门解析</title>
<style>
  :root{
    --bg:#0a0c10; --panel:#11151d; --panel2:#161b25; --line:#232a36;
    --text:#e6edf6; --dim:#8b97a8; --green:#3ddc84; --cyan:#4cc2ff;
    --mag:#c084fc; --yellow:#fbbf24; --red:#f87171; --gray:#6b7688;
  }
  *{box-sizing:border-box}
  body{margin:0;background:radial-gradient(1200px 600px at 50% -200px,#16202e 0%,var(--bg) 60%);
       color:var(--text);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
       min-height:100vh;padding:40px 20px 60px}
  .wrap{max-width:860px;margin:0 auto}
  h1{font-size:24px;margin:0 0 6px;letter-spacing:.3px}
  h1 span{color:var(--green)}
  .sub{color:var(--dim);font-size:13px;margin-bottom:26px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}
  .row{display:flex;gap:10px}
  input[type=text]{flex:1;background:var(--panel2);border:1px solid var(--line);color:var(--text);
       border-radius:10px;padding:13px 14px;font-size:14px;outline:none;font-family:ui-monospace,Menlo,monospace}
  input[type=text]:focus{border-color:#33507a;box-shadow:0 0 0 3px #1d2b3f}
  button{cursor:pointer;border:none;border-radius:10px;padding:13px 22px;font-size:14px;font-weight:600;
       background:linear-gradient(180deg,#3ddc84,#28b46a);color:#04150c;transition:.15s}
  button:hover{filter:brightness(1.08)}
  button:disabled{opacity:.55;cursor:not-allowed}
  .opts{display:flex;align-items:center;gap:18px;margin-top:12px;color:var(--dim);font-size:13px}
  .switch{display:flex;align-items:center;gap:8px;cursor:pointer;user-select:none}
  .switch input{appearance:none;width:36px;height:20px;background:#2a323f;border-radius:20px;position:relative;transition:.2s;cursor:pointer}
  .switch input:checked{background:#1f6f45}
  .switch input:before{content:"";position:absolute;width:16px;height:16px;border-radius:50%;background:#8b97a8;top:2px;left:2px;transition:.2s}
  .switch input:checked:before{left:18px;background:var(--green)}
  #out{margin-top:22px}
  .final{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:16px}
  .badge{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px;border:1px solid var(--line);
       color:var(--dim);margin-left:8px;vertical-align:2px}
  .badge.ok{color:var(--green);border-color:#1f6f45;background:#0e2318}
  .badge.warn{color:var(--yellow);border-color:#5a4415;background:#231b0a}
  .badge.err{color:var(--red);border-color:#5b2020;background:#230e0e}
  .url{font-family:ui-monospace,Menlo,monospace;font-size:14px;word-break:break-all;margin:10px 0 4px;color:#bfe9d0}
  .url a{color:inherit;text-decoration:none;border-bottom:1px dashed #2c5a44}
  .meta{color:var(--dim);font-size:12px;margin-top:8px}
  .copy{background:transparent;border:1px solid var(--line);color:var(--dim);padding:6px 12px;font-size:12px;border-radius:8px;font-weight:500}
  .copy:hover{color:var(--text);border-color:#33507a}
  .trace{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden}
  .trace h3{margin:0;padding:13px 18px;font-size:12px;letter-spacing:.6px;color:var(--dim);
       text-transform:uppercase;border-bottom:1px solid var(--line);font-weight:600}
  .step{display:grid;grid-template-columns:52px 1fr;gap:10px;padding:10px 18px;border-bottom:1px solid #171d27;
       font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
  .step:last-child{border-bottom:none}
  .tag{font-size:11px;text-align:right;padding-top:2px}
  .k-request .tag{color:var(--cyan)} .k-redirect .tag{color:var(--mag)}
  .k-extract .tag{color:var(--green)} .k-click .tag{color:var(--yellow)}
  .k-note .tag,.k-note .u{color:var(--gray)} .k-blocked .tag,.k-error .tag{color:var(--red)}
  .u{word-break:break-all;color:#cfd8e6}
  .d{color:var(--dim);font-size:11.5px;margin-top:2px}
  .st{color:var(--gray);font-size:11px;margin-left:6px}
  .empty{color:var(--dim);font-size:13px;text-align:center;padding:26px}
  .spin{display:inline-block;width:13px;height:13px;border:2px solid #2a323f;border-top-color:var(--green);
       border-radius:50%;animation:s .7s linear infinite;margin-right:7px;vertical-align:-2px}
  @keyframes s{to{transform:rotate(360deg)}}
  .note{color:var(--dim);font-size:12px;margin-top:14px;line-height:1.7}
  .note b{color:#a9b6c8;font-weight:600}
  code{background:#171d27;padding:1px 6px;border-radius:5px;font-size:11.5px;color:#9fb3c8}
</style>
</head>
<body>
<div class="wrap">
  <h1>Link <span>Resolver</span></h1>
  <div class="sub">短链 → 广告门 → 真实终点。HTTP 快路径优先，卡住就上 Chromium 点掉倒计时。</div>

  <div class="card">
    <div class="row">
      <input id="u" type="text" placeholder="粘贴短链，例如 https://tinyurl.com/xxxx 或 https://gplinks.co/xxxx" autocomplete="off" spellcheck="false">
      <button id="go">解析</button>
    </div>
    <div class="opts">
      <label class="switch"><input type="checkbox" id="browser" checked> 浏览器模式（慢但能过 JS 广告门）</label>
      <span>·</span>
      <span>支持一次粘贴多个链接？用 CLI：<code>python -m linkresolver url1 url2</code></span>
    </div>
  </div>

  <div id="out"><div class="empty">还没有解析结果</div></div>

  <div class="note">
    <b>说明</b>：本工具只做「展开跳转链」——顺着服务器返回的 30x、meta refresh、JS location、base64 参数、
    表单提交，一路走到终点；需要交互的页面用无头 Chromium 点「继续」。
    遇到 Cloudflare 人机验证、登录墙、地区限制时它会如实告诉你卡在哪，而不是编一个链接给你。
  </div>
</div>

<script>
const $ = s => document.querySelector(s);
const out = $('#out'), btn = $('#go'), inp = $('#u');
let t0 = 0, timer = null;

const TAG = {request:'GET',redirect:'→',extract:'解出',click:'点击',note:'备注',blocked:'拦截',error:'错误'};

function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function tick(){
  const s = ((Date.now()-t0)/1000).toFixed(1);
  $('#loading') && ($('#loading').textContent = `解析中 ${s}s …`);
}

async function go(){
  const url = inp.value.trim();
  if(!url) { inp.focus(); return; }
  btn.disabled = true; t0 = Date.now();
  out.innerHTML = `<div class="final"><span class="spin"></span><span id="loading">解析中 0.0s …</span>
      <div class="meta">浏览器模式要等倒计时，通常 5–25 秒</div></div>`;
  timer = setInterval(tick, 100);
  try{
    const r = await fetch('/api/resolve', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({url, browser: $('#browser').checked})
    });
    const d = await r.json();
    render(d);
  }catch(e){
    out.innerHTML = `<div class="final"><span class="badge err">失败</span><div class="url">${esc(e.message)}</div></div>`;
  }finally{
    clearInterval(timer); btn.disabled = false;
  }
}

function render(d){
  const okBadge = d.ok ? `<span class="badge ok">已到终点</span>`
                : (d.blocked ? `<span class="badge err">被拦截</span>` : `<span class="badge warn">未到达</span>`);
  const method = {http:'HTTP 快路径', browser:'浏览器模式', rules:'规则命中'}[d.method] || d.method || '—';
  let html = `<div class="final">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
      <div style="min-width:0">
        <span class="badge">${esc(method)}</span>${okBadge}
        <div class="url">${d.final ? `<a href="${esc(d.final)}" target="_blank" rel="noreferrer noopener">${esc(d.final)}</a>` : '—'}</div>
        <div class="meta">${esc(d.note||'')} · 耗时 ${d.elapsed}s</div>
      </div>
      ${d.final ? `<button class="copy" onclick="navigator.clipboard.writeText(${JSON.stringify(d.final)});this.textContent='已复制'">复制</button>` : ''}
    </div></div>`;

  if(d.steps && d.steps.length){
    html += `<div class="trace"><h3>跳转轨迹 · ${d.steps.length} 步</h3>`;
    d.steps.forEach(s=>{
      const st = [s.status?('HTTP '+s.status):'', s.elapsed?`${s.elapsed}s`:''].filter(Boolean).join(' · ');
      html += `<div class="step k-${s.kind}">
        <div class="tag">${TAG[s.kind]||s.kind}</div>
        <div><div class="u">${esc(s.url)}</div>
        ${s.detail?`<div class="d">${esc(s.detail)}</div>`:''}
        ${st?`<div class="st">${st}</div>`:''}</div></div>`;
    });
    html += `</div>`;
  }
  out.innerHTML = html;
}

btn.onclick = go;
inp.addEventListener('keydown', e => { if(e.key === 'Enter') go(); });
</script>
</body>
</html>"""
