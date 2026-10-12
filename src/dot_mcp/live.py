"""Live HTTP shell and authenticated websocket transport (no tokens in query/logs)."""
import asyncio
import json
import time
import uuid
from urllib.parse import urlsplit
from starlette.responses import HTMLResponse, Response
from starlette.routing import Route, WebSocketRoute

HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
           "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
           "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"}

PAGE = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>dot-mcp Live</title><style>
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:#101318;color:#edf1f5;font:16px system-ui}header{padding:14px;display:flex;gap:12px;flex-wrap:wrap;align-items:center}button,input{font:inherit;padding:10px 14px;min-height:44px;border-radius:8px}button{cursor:pointer;border:1px solid #405368;background:#253445;color:#edf1f5}button:disabled{opacity:.45;cursor:default}button:focus-visible,input:focus{outline:3px solid #8ed5bf;outline-offset:2px}input{border:1px solid #405368;background:#151c25;color:#edf1f5}#screen{display:block;width:100%;max-width:1280px;margin:auto;touch-action:none;outline:none;background:#222}#status{color:#b4c5d9}.note{margin:0;padding:8px 14px;color:#b4c5d9;font-size:13px}#text{min-width:180px;flex:1}#fps{font-variant-numeric:tabular-nums}
</style></head><body><header><strong>dot-mcp <span style="color:#8ed5bf">Live</span></strong><span id="status" role="status" aria-live="polite">Authenticate to join</span><span id="fps">0 fps</span><button id="take" disabled>Take control</button><button id="release" disabled>Release</button><input id="text" aria-label="Type into the controlled browser" placeholder="Type directly here, not in AI chat" disabled autocomplete="off"><button id="send" disabled>Type</button></header><p class="note">Private session. A control link grants clicks and typing. Live video may show personal data. Close the browser to revoke both links. 60 fps is a target; actual rate is shown.</p><form id="auth" style="padding:14px;display:flex;gap:8px;flex-wrap:wrap"><label for="credential">Owner token (required on every visit)</label><input id="credential" type="password" autocomplete="off" required placeholder="Owner secret or OAuth access token"><button>Authenticate</button><span>Not stored. Session lasts up to 15 minutes.</span></form><canvas aria-label="Remote browser viewport" id="screen" width="1280" height="720" tabindex="0"></canvas><script>
const token=new URLSearchParams(location.hash.slice(1)).get('token');history.replaceState(null,'',location.pathname);const canvas=document.querySelector('#screen'),ctx=canvas.getContext('2d');let controlling=false,received=0,displayed=0,start=performance.now(),lastMove=0,privacy=false;const status=document.querySelector('#status'),take=document.querySelector('#take'),release=document.querySelector('#release'),text=document.querySelector('#text'),send=document.querySelector('#send');
let ws={readyState:0};document.querySelector('#auth').onsubmit=e=>{e.preventDefault();const credential=document.querySelector('#credential').value;document.querySelector('#credential').value='';ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/live/ws');bind(credential)};function post(x){if(ws.readyState===1)ws.send(JSON.stringify(x))}function controls(on){controlling=on;release.disabled=!on;text.disabled=!on;send.disabled=!on;take.disabled=on;status.textContent=on?'You control the browser':'Watching';}
function bind(credential){ws.onopen=()=>{post({token,credential});credential=null};ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.type==='ready'){document.querySelector('#auth').hidden=true;status.textContent=m.role==='control'?'Watching (control available)':'View only';take.disabled=m.role!=='control'}else if(m.type==='frame'){if(privacy)return;let image=new Image();image.onload=()=>{if(!privacy){ctx.drawImage(image,0,0,1280,720);displayed++}post({action:"frame_ack",sequence:m.sequence})};image.onerror=()=>post({action:"frame_ack",sequence:m.sequence});image.src='data:image/jpeg;base64,'+m.data;received++;document.querySelector('#fps').textContent=(received*1000/(performance.now()-start)).toFixed(1)+' received fps / '+m.captured_fps+' captured / '+(displayed*1000/(performance.now()-start)).toFixed(1)+' displayed'}else if(m.type==='privacy'){privacy=true;ctx.clearRect(0,0,1280,720);status.textContent=m.message}else if(m.type==='control'){controls(m.active)}else if(m.type==='error'){status.textContent=m.message}else if(m.type==='closed'){status.textContent='Session closed';ws.close()}};ws.onclose=()=>{controls(false);take.disabled=true;status.textContent='Disconnected or expired';document.querySelector('#auth').hidden=false}};take.onclick=()=>post({action:'claim'});release.onclick=()=>post({action:'release'});send.onclick=()=>{post({action:'text',text:text.value});text.value='';canvas.focus()};text.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();send.click()}};
function pos(e){const r=canvas.getBoundingClientRect();return{x:Math.max(0,Math.min(1280,(e.clientX-r.left)*1280/r.width)),y:Math.max(0,Math.min(720,(e.clientY-r.top)*720/r.height))}}
canvas.onpointerdown=e=>{if(!controlling)return;e.preventDefault();canvas.focus();canvas.setPointerCapture(e.pointerId);post({action:'mouse',kind:'down',...pos(e)})};canvas.onpointerup=e=>{if(controlling)post({action:'mouse',kind:'up',...pos(e)})};canvas.onpointercancel=e=>{if(controlling)post({action:'mouse',kind:'up',...pos(e)})};canvas.onpointermove=e=>{if(!controlling||performance.now()-lastMove<16)return;lastMove=performance.now();post({action:'mouse',kind:'move',...pos(e)})};canvas.addEventListener('wheel',e=>{if(!controlling)return;e.preventDefault();post({action:'wheel',x:e.deltaX,y:e.deltaY})},{passive:false});const keys=new Set(['Enter','Tab','Escape','Backspace','Delete','ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End','PageUp','PageDown','Shift','Control','Alt']);canvas.onkeydown=e=>{if(!controlling)return;e.preventDefault();if(keys.has(e.key))post({action:'key',kind:'down',key:e.key});else if(e.key.length===1&&!e.ctrlKey&&!e.altKey)post({action:'text',text:e.key})};canvas.onkeyup=e=>{if(controlling&&keys.has(e.key)){e.preventDefault();post({action:'key',kind:'up',key:e.key})}};window.onblur=()=>{if(controlling)post({action:'release'})};
</script></body></html>'''


def routes(get_browser, expected_origin, validate_owner):
    async def page(request):
        return HTMLResponse(PAGE, headers=HEADERS)
    async def bridge(coro, browser):
        import asyncio
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, browser.loop))
    attempts = {}
    async def websocket(ws):
        # Exact origin includes scheme/port. Never accept arbitrary pages connecting with a stolen token.
        if ws.headers.get('origin') != expected_origin():
            await ws.close(code=1008)
            return
        ip = ws.client.host if ws.client else "unknown"
        now = time.monotonic()
        hits = [t for t in attempts.get(ip, []) if now-t < 60]
        if len(hits) >= 10:
            await ws.close(code=1008)
            return
        attempts[ip] = hits + [now]
        if len(attempts) > 1000:
            for address in list(attempts):
                if not any(now-t < 60 for t in attempts[address]): attempts.pop(address, None)
        await ws.accept()
        browser = get_browser(create=False)
        if browser is None:
            await ws.close(code=1008)
            return
        client_id = uuid.uuid4().hex
        task = None
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=5)
            if len(raw) > 4096: raise ValueError('invalid auth payload')
            auth = json.loads(raw)
            credential = auth.get('credential', '')
            if not isinstance(credential, str) or not validate_owner(credential):
                raise ValueError('owner authentication required')
            auth_deadline = time.monotonic() + 900
            token = auth.get('token', '')
            if not isinstance(token, str): raise ValueError('invalid auth payload')
            role, q = await bridge(browser.connect(token, client_id), browser)
            ack = asyncio.Event()
            outstanding = set()
            await ws.send_json({'type': 'ready', 'role': role})
            async def sender():
                while browser.authorize(token) and validate_owner(credential) and time.monotonic() < auth_deadline:
                    try:
                        packet = await asyncio.wait_for(bridge(q.get(), browser), timeout=2)
                    except asyncio.TimeoutError:
                        continue
                    if packet['type'] == 'frame':
                        ack.clear()
                        outstanding.add(packet.get('sequence'))
                    await ws.send_json(packet)
                    if packet['type'] == 'frame' and len(outstanding) >= 4:
                        # Bounded four-frame window tolerates RTT; latest-frame queue absorbs
                        # slow decode/network. Do not turn a slow viewer into latency.
                        try: await asyncio.wait_for(ack.wait(), timeout=5)
                        except asyncio.TimeoutError:
                            await ws.close(code=1008)
                            return
                    if packet['type'] == 'closed': return
                await ws.close(code=1008)
            task = asyncio.create_task(sender())
            hits = []
            while browser.authorize(token) and validate_owner(credential) and time.monotonic() < auth_deadline:
                raw = await ws.receive_text()
                if time.monotonic() >= auth_deadline or not browser.authorize(token) or not validate_owner(credential): break
                if len(raw) > 12000: raise ValueError('input too large')
                now = time.monotonic()
                hits = [t for t in hits if now-t < 1]
                if len(hits) >= 120: raise ValueError('input rate limit exceeded')
                hits.append(now)
                message = json.loads(raw)
                if isinstance(message, dict) and message.get('action') == 'frame_ack':
                    sequence = message.get('sequence')
                    if isinstance(sequence, int) and sequence in outstanding:
                        outstanding.discard(sequence);ack.set()
                    continue
                try:
                    result = await bridge(browser.input(client_id, role, message), browser)
                    await ws.send_json(result)
                except ValueError as exc:
                    await ws.send_json({'type':'error', 'message':('vault privacy mode: input unavailable' if browser.sensitive else str(exc)[:500])})
        except Exception:
            pass
        finally:
            if task:
                task.cancel()
                try: await task
                except (asyncio.CancelledError, Exception): pass
            await bridge(browser.disconnect(client_id), browser)
            try: await ws.close()
            except Exception: pass
    return [Route('/live', page), WebSocketRoute('/live/ws', websocket)]
