"""داشبورد FastAPI: وضعیت سیستم، درصد پیشرفت، آمار، مدیریت متن‌ها و کنترل ارسال."""
from __future__ import annotations

import asyncio
import hmac
import json
import urllib.request

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

import config
import db


def _bot_stats_sync():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8090/stats", timeout=3) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return {"bot_users": 0, "bot_started": 0}

app = FastAPI(title="Store Outreach Dashboard")


def _authed(request: Request):
    if not config.DASH_TOKEN:
        return False  # بدونِ توکن، بسته (fail-closed) — تا کنترلِ یوزربات هرگز باز نباشد
    tok = (
        request.query_params.get("token")
        or request.cookies.get("dash_token")
        or request.headers.get("X-Dash-Token")
    )
    return hmac.compare_digest(str(tok or ""), str(config.DASH_TOKEN or ""))


def _guard(request: Request):
    if not _authed(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return None


@app.get("/api/state")
async def api_state(request: Request):
    g = _guard(request)
    if g:
        return g
    bot = await asyncio.to_thread(_bot_stats_sync)
    return {
        "stats": db.stats(),
        "templates": db.list_templates(),
        "recent": db.recent_sends(25),
        "telegram_authorized": db.get_meta("tg_authorized", "0") == "1",
        "telegram_ready": config.telegram_ready(),
        "bot": bot,
        "settings": db.current_settings(),
        "autoreply": db.get_meta("autoreply", "off"),
        "followup": db.get_meta("followup", "off"),
        "autoreply_today": db.autoreply_today(),
        "autoreplies": db.recent_autoreplies(15),
        "no_telegram_count": db.no_telegram_count(),
    }


@app.get("/api/export/no-telegram")
async def api_export_no_telegram(request: Request):
    """لیستِ «بدونِ تلگرام» را به‌صورت CSV می‌دهد (برای ارسالِ بعدی در واتساپ)."""
    g = _guard(request)
    if g:
        return g
    import os

    from fastapi.responses import FileResponse
    path = os.path.join("data", "no_telegram.csv")
    await asyncio.to_thread(db.export_no_telegram_csv, path)
    return FileResponse(path, filename="no_telegram.csv", media_type="text/csv")


@app.post("/api/notify")
async def api_notify(request: Request):
    """مغز نتیجهٔ تاییدِ رسید را اینجا می‌فرستد تا به مشتری در همین یوزربات اعلام شود (با توکنِ مغز)."""
    tok = request.headers.get("X-SB-Token") or request.query_params.get("token")
    if not config.SALE_BRAIN_TOKEN or not hmac.compare_digest(str(tok or ""), str(config.SALE_BRAIN_TOKEN)):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = await request.json()
    import autoreply
    ok = await autoreply.notify(body.get("customer_id"), body.get("text"))
    return {"ok": bool(ok)}


@app.post("/api/settings")
async def api_settings(request: Request):
    g = _guard(request)
    if g:
        return g
    body = await request.json()
    return {"ok": True, "settings": db.save_settings(body)}


@app.post("/api/control")
async def api_control(request: Request):
    g = _guard(request)
    if g:
        return g
    body = await request.json()
    action = (body.get("action") or "").strip()
    if action == "start":
        db.set_meta("sender_state", "running")
        db.set_meta("paused_reason", "")
        db.set_meta("account_locked", "0")  # شروعِ دستی → قفلِ اکانت هم باز شود
    elif action == "pause":
        db.set_meta("sender_state", "paused")
        db.set_meta("paused_reason", "توقف دستی")
    elif action == "autoreply_on":
        db.set_meta("autoreply", "on")
    elif action == "autoreply_off":
        db.set_meta("autoreply", "off")
    elif action == "followup_on":
        db.set_meta("followup", "on")
    elif action == "followup_off":
        db.set_meta("followup", "off")
    return {"ok": True, "state": db.get_meta("sender_state")}


@app.post("/api/template")
async def api_template(request: Request):
    g = _guard(request)
    if g:
        return g
    body = await request.json()
    action = (body.get("action") or "").strip()
    if action == "add":
        db.add_template(body.get("body", ""))
    elif action == "toggle":
        db.toggle_template(int(body.get("id")), bool(body.get("enabled")))
    elif action == "delete":
        db.delete_template(int(body.get("id")))
    return {"ok": True, "templates": db.list_templates()}


@app.post("/api/tx")
async def api_tx(request: Request):
    """افزودنِ یک پیامِ سفارشی به صفِ تراکنشیِ اولویت‌دار (بازیابیِ پرداخت و …)."""
    g = _guard(request)
    if g:
        return g
    body = await request.json()
    status = db.tx_enqueue(body.get("phone", ""), body.get("text", ""), body.get("key", ""))
    return {"ok": True, "added": status == "added", "exists": status == "exists", "status": status, "tx": db.tx_stats()}


@app.get("/api/tx/status")
async def api_tx_status(request: Request):
    """وضعیتِ یک پیامِ تراکنشی بر اساسِ کلید (بازخوردِ تحویل به سفارش‌بات)."""
    g = _guard(request)
    if g:
        return g
    key = request.query_params.get("key", "")
    return {"ok": True, "status": db.tx_status(key)}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not _authed(request):
        return HTMLResponse(_LOGIN_HTML)
    resp = HTMLResponse(_DASH_HTML)
    tok = request.query_params.get("token")
    if tok and hmac.compare_digest(str(tok), str(config.DASH_TOKEN or "")):
        resp.set_cookie("dash_token", tok, httponly=True, samesite="lax")
    return resp


async def serve():
    cfg = uvicorn.Config(app, host=config.DASH_HOST, port=config.DASH_PORT, log_level="warning", log_config=None)
    server = uvicorn.Server(cfg)
    print(f"[dash] داشبورد روی http://{config.DASH_HOST}:{config.DASH_PORT} فعال شد.")
    await server.serve()


_LOGIN_HTML = """<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ورود</title>
<style>body{font-family:Tahoma;background:#0e0e10;color:#eee;display:flex;height:100vh;align-items:center;justify-content:center;margin:0}
.box{background:#17171b;padding:30px;border-radius:14px;border:1px solid #2a2a30;text-align:center}
input{padding:10px;border-radius:8px;border:1px solid #333;background:#0e0e10;color:#eee;width:240px}
button{margin-top:12px;padding:10px 20px;border:none;border-radius:8px;background:#caa15a;color:#111;font-weight:bold;cursor:pointer}</style></head>
<body><div class="box"><h3 style="color:#caa15a">داشبورد پیام‌رسانی</h3>
<form onsubmit="location.href='/?token='+encodeURIComponent(document.getElementById('t').value);return false;">
<input id="t" placeholder="توکن ورود" autofocus><br><button>ورود</button></form></div></body></html>
"""

_DASH_HTML = r"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>داشبورد پیام‌رسانی</title>
<style>
:root{--gold:#caa15a;--bg:#0e0e10;--card:#17171b;--bd:#2a2a30;--mut:#9a9aa3}
*{box-sizing:border-box}
body{font-family:Tahoma,sans-serif;background:var(--bg);color:#eee;margin:0;padding:18px}
h1{color:var(--gold);font-size:20px;margin:0 0 4px}
.sub{color:var(--mut);font-size:12px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px}
.card .n{font-size:26px;font-weight:bold}
.card .l{color:var(--mut);font-size:12px;margin-top:4px}
.gold{color:var(--gold)} .green{color:#4caf50} .red{color:#e5675a} .gray{color:var(--mut)}
.bar{height:18px;background:#000;border-radius:10px;overflow:hidden;border:1px solid var(--bd)}
.bar>div{height:100%;background:linear-gradient(90deg,#caa15a,#e8c98a);width:0;transition:width .5s}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.badge{padding:4px 12px;border-radius:20px;font-size:13px;font-weight:bold}
.b-run{background:#143d20;color:#5fd97f} .b-pause{background:#3a3a1a;color:#e3d36a} .b-spam{background:#4a1d1d;color:#ef8a7e}
.sec{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:16px;margin-bottom:16px}
.sec h2{font-size:15px;margin:0 0 12px;color:var(--gold)}
button{border:none;border-radius:8px;padding:8px 16px;font-weight:bold;cursor:pointer;font-family:inherit}
.btn-go{background:#2e7d32;color:#fff} .btn-stop{background:#b23b30;color:#fff} .btn-sm{padding:5px 10px;font-size:12px}
.tpl{background:#0e0e10;border:1px solid var(--bd);border-radius:8px;padding:10px;margin-bottom:8px}
.tpl.off{opacity:.45}
.tpl .body{font-size:13px;line-height:1.8;margin-bottom:6px}
.tpl .meta{display:flex;gap:8px;align-items:center;font-size:11px;color:var(--mut)}
textarea{width:100%;background:#0e0e10;border:1px solid var(--bd);border-radius:8px;color:#eee;padding:10px;font-family:inherit;font-size:13px;min-height:60px}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:right;padding:7px 6px;border-bottom:1px solid var(--bd)}
th{color:var(--mut);font-weight:normal}
.warn{background:#4a1d1d;color:#ef8a7e;padding:8px 12px;border-radius:8px;font-size:13px;margin-bottom:12px}
.fld{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--mut)}
.fld input{width:130px;padding:8px;border-radius:8px;border:1px solid var(--bd);background:#0e0e10;color:#eee;font-family:inherit;font-size:14px}
</style></head>
<body>
<h1>📡 داشبورد پیام‌رسانی</h1>
<div class="sub" id="clock">—</div>

<div id="banner"></div>

<div class="grid" id="cards"></div>

<div class="sec">
  <h2>پیشرفت کل کمپین</h2>
  <div class="bar"><div id="pbar"></div></div>
  <div class="row" style="justify-content:space-between;margin-top:8px;font-size:13px">
    <span id="ptext">—</span>
    <span class="gray" id="todaytext">—</span>
  </div>
</div>

<div class="sec">
  <h2>وضعیت موتور ارسال</h2>
  <div class="row" style="justify-content:space-between">
    <div class="row">
      <span id="statebadge" class="badge b-pause">—</span>
      <span class="gray" id="statereason"></span>
    </div>
    <div class="row">
      <button class="btn-go" onclick="ctrl('start')">▶ شروع ارسال</button>
      <button class="btn-stop" onclick="ctrl('pause')">⏸ توقف</button>
    </div>
  </div>
</div>

<div class="sec">
  <h2>تنظیماتِ سرعتِ ارسال (زنده)</h2>
  <div class="row" style="gap:14px;flex-wrap:wrap">
    <label class="fld">تعداد پیام در هر بازه<input id="set_burst" type="number" min="1"></label>
    <label class="fld">فاصلهٔ بین پیام‌ها — کمینه (ثانیه)<input id="set_dmin" type="number" min="5"></label>
    <label class="fld">فاصلهٔ بین پیام‌ها — بیشینه (ثانیه)<input id="set_dmax" type="number" min="5"></label>
    <label class="fld">استراحتِ بین بازه‌ها (دقیقه)<input id="set_pause" type="number" min="0"></label>
    <label class="fld">سقفِ پیام در روز<input id="set_cap" type="number" min="1"></label>
  </div>
  <div style="margin-top:10px">
    <button class="btn-sm" style="background:var(--gold);color:#111" onclick="saveSettings()">💾 ذخیرهٔ تنظیمات</button>
    <span id="set_msg" class="gray" style="margin-right:10px;font-size:12px"></span>
  </div>
</div>

<div class="sec">
  <h2>پاسخِ خودکارِ دایرکت (مغزِ فروش)</h2>
  <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:10px">
    <div class="row" style="gap:12px;flex-wrap:wrap">
      <span>پاسخِ خودکار: <span id="ar_badge" class="badge b-pause">—</span></span>
      <button class="btn-go btn-sm" onclick="ctrl('autoreply_on')">روشن</button>
      <button class="btn-stop btn-sm" onclick="ctrl('autoreply_off')">خاموش</button>
      <span class="gray">|</span>
      <span>فالوآپ: <span id="fu_badge" class="badge b-pause">—</span></span>
      <button class="btn-go btn-sm" onclick="ctrl('followup_on')">روشن</button>
      <button class="btn-stop btn-sm" onclick="ctrl('followup_off')">خاموش</button>
    </div>
    <span class="gray">پاسخِ امروز: <b id="ar_today">0</b></span>
  </div>
  <table style="margin-top:12px"><thead><tr><th>کاربر</th><th>پیامِ مشتری</th><th>پاسخِ ربات</th><th>زمان</th></tr></thead>
  <tbody id="ar_rows"></tbody></table>
</div>

<div class="sec">
  <h2>متن‌های پیام (چرخشی)</h2>
  <div id="tpls"></div>
  <textarea id="newtpl" placeholder="متن جدید... (می‌تونی {name} بذاری تا با نام مخاطب جایگزین شه)"></textarea>
  <button class="btn-sm" style="background:var(--gold);color:#111;margin-top:8px" onclick="addTpl()">+ افزودن متن</button>
</div>

<div class="sec">
  <h2>آخرین ارسال‌ها</h2>
  <table><thead><tr><th>شماره</th><th>نام</th><th>وضعیت</th><th>زمان</th></tr></thead>
  <tbody id="recent"></tbody></table>
</div>

<script>
const STATE_LABEL = {running:['در حال ارسال','b-run'], paused:['متوقف','b-pause']};
const ST_FA = {sent:'ارسال شد',failed:'ناموفق',no_telegram:'با شماره پیدا نشد',optout:'انصراف',pending:'در صف'};
const SET_MAP = {set_burst:'burst_size',set_dmin:'delay_min',set_dmax:'delay_max',set_pause:'burst_pause_min',set_cap:'daily_cap'};
let setLoaded=false;
function esc(s){var d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}

async function load(){
  let r;
  try{ r = await (await fetch('/api/state')).json(); }catch(e){ return; }
  if(r.error) return;
  const s = r.stats;

  // بنر هشدار تلگرام
  let bn='';
  if(!r.telegram_ready) bn='<div class="warn">⚠️ تلگرام پیکربندی نشده — TG_API_ID/HASH/PHONE را در .env بگذار. (ارسال غیرفعال، فقط نمایش)</div>';
  else if(!r.telegram_authorized) bn='<div class="warn">⚠️ هنوز وارد تلگرام نشده‌ای — یک‌بار <b>python login.py</b> را روی سرور اجرا کن.</div>';
  document.getElementById('banner').innerHTML = bn;

  // کارت‌ها
  const b = r.bot || {};
  const cards=[['کل مخاطبین',s.total,'gold'],['ارسال‌شده',s.sent,'green'],['سین‌شده',s.seen||0,'gold'],['در صف',s.pending,'gray'],
    ['عضو ربات',b.bot_started||0,'green'],['کاربران فعال',b.bot_users||0,'green'],
    ['از کانتکت/چت',s.account||0,'gold'],
    ['ناموفق',s.failed,'red'],['با شماره پیدا نشد',s.no_telegram,'gray'],['انصراف',s.optout,'gray']];
  document.getElementById('cards').innerHTML = cards.map(c=>
    `<div class="card"><div class="n ${c[2]}">${c[1].toLocaleString('fa')}</div><div class="l">${c[0]}</div></div>`).join('')
    + `<div class="card" style="display:flex;flex-direction:column;justify-content:center;gap:6px">`
    + `<a href="/api/export/no-telegram" style="color:var(--gold);font-weight:bold;text-decoration:none">⬇ دانلودِ لیستِ «بدونِ تلگرام»</a>`
    + `<div class="l">برای ارسالِ بعدی در واتساپ (${(s.no_telegram||0).toLocaleString('fa')} نفر)</div></div>`;

  // پیشرفت
  document.getElementById('pbar').style.width = s.progress_pct + '%';
  document.getElementById('ptext').innerHTML = `<b>${s.progress_pct}%</b> — ${s.processed.toLocaleString('fa')} از ${s.total.toLocaleString('fa')} پردازش شد`;
  document.getElementById('todaytext').textContent = `امروز ${s.sent_today} از سقف ${s.today_cap} · روز گرم‌کردن ${s.warming_day}`;

  // وضعیت موتور
  const lbl = STATE_LABEL[s.state] || ['نامشخص','b-pause'];
  const sb = document.getElementById('statebadge'); sb.textContent = lbl[0]; sb.className = 'badge '+lbl[1];
  document.getElementById('statereason').textContent = s.paused_reason || '';

  // متن‌ها
  document.getElementById('tpls').innerHTML = r.templates.map(t=>
    `<div class="tpl ${t.enabled?'':'off'}"><div class="body">${esc(t.body)}</div>
     <div class="meta"><span>ارسال‌شده: ${t.sent_count}</span>
     <button class="btn-sm" style="background:#333;color:#eee" onclick="toggleTpl(${t.id},${t.enabled?0:1})">${t.enabled?'غیرفعال':'فعال'}</button>
     <button class="btn-sm" style="background:#5a2020;color:#eee" onclick="delTpl(${t.id})">حذف</button></div></div>`).join('') || '<div class="gray">هنوز متنی نیست.</div>';

  // اخیر
  document.getElementById('recent').innerHTML = r.recent.map(x=>
    `<tr><td>${esc(x.phone)}</td><td>${esc(x.name)}</td><td>${ST_FA[x.status]||esc(x.status)}${x.error?' · <span class="gray">'+esc(x.error)+'</span>':''}</td><td class="gray">${esc(x.created_at)}</td></tr>`).join('') || '<tr><td colspan="4" class="gray">هنوز ارسالی نبوده.</td></tr>';

  // تنظیماتِ سرعت: فقط یک‌بار پر کن تا تایپِ کاربر را خراب نکند
  if(!setLoaded && r.settings){
    for(const id in SET_MAP){ const el=document.getElementById(id); if(el) el.value=r.settings[SET_MAP[id]]; }
    setLoaded=true;
  }

  // پاسخِ خودکار / فالوآپ
  const arOn=r.autoreply=='on', fuOn=r.followup=='on';
  const arb=document.getElementById('ar_badge'); if(arb){arb.textContent=arOn?'روشن':'خاموش';arb.className='badge '+(arOn?'b-run':'b-pause');}
  const fub=document.getElementById('fu_badge'); if(fub){fub.textContent=fuOn?'روشن':'خاموش';fub.className='badge '+(fuOn?'b-run':'b-pause');}
  const art=document.getElementById('ar_today'); if(art) art.textContent=(r.autoreply_today||0).toLocaleString('fa');
  const arr=document.getElementById('ar_rows'); if(arr) arr.innerHTML=(r.autoreplies||[]).map(x=>`<tr><td>${esc(x.name)}</td><td>${esc((x.incoming||'').slice(0,40))}</td><td>${esc((x.reply||'').slice(0,50))}</td><td class="gray">${esc(x.at)}</td></tr>`).join('')||'<tr><td colspan=4 class=gray>هنوز پاسخی نیست.</td></tr>';

  document.getElementById('clock').textContent = 'به‌روزرسانی: ' + new Date().toLocaleTimeString('fa');
}
async function saveSettings(){
  const body={};
  for(const id in SET_MAP){ body[SET_MAP[id]] = +document.getElementById(id).value; }
  const msg=document.getElementById('set_msg'); msg.textContent='در حال ذخیره…';
  try{
    const j=await (await fetch('/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
    if(j.settings){ for(const id in SET_MAP){ document.getElementById(id).value=j.settings[SET_MAP[id]]; } }
    msg.textContent='✅ ذخیره شد — بلافاصله اعمال می‌شود';
  }catch(e){ msg.textContent='خطا در ذخیره'; }
}
async function ctrl(a){ await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:a})}); load(); }
async function addTpl(){ const t=document.getElementById('newtpl'); if(!t.value.trim())return; await fetch('/api/template',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'add',body:t.value})}); t.value=''; load(); }
async function toggleTpl(id,en){ await fetch('/api/template',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'toggle',id:id,enabled:!!en})}); load(); }
async function delTpl(id){ if(!confirm('حذف این متن؟'))return; await fetch('/api/template',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'delete',id:id})}); load(); }
load(); setInterval(load, 4000);
</script>
</body></html>
"""
