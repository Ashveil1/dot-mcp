"""Optional Chromium integration using only a local fixture page."""
import asyncio
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
pytest.importorskip('playwright')
from starlette.applications import Starlette
from starlette.testclient import TestClient
from dot_mcp.browser import LocalBrowser, BrowserError
from dot_mcp.live import routes

HTML = b'''<!doctype html><title>Browser test</title><style>body{font:24px system-ui;padding:30px;background:#eee}canvas{background:#123}input{font:24px system-ui}#hold{padding:25px}#space{height:1800px}</style><h1>Local browser fixture</h1><input name="message" placeholder="message"><input type="password" name="password"><input type="checkbox" name="agree"><button id="hold">Hold here</button><button id="off" disabled>Off</button><select name="choice"><option value="a">Alpha</option><option value="b">Beta</option></select><div id="out">ready</div><div id="late"></div><canvas id="anim" width="600" height="160"></canvas><div id="space">Scroll test</div><script>
let n=0;let c=anim.getContext('2d');function draw(){c.fillStyle='#123';c.fillRect(0,0,600,160);c.fillStyle='#7cf';c.fillRect((n++*3)%550,30,50,60);requestAnimationFrame(draw)}draw();hold.onpointerdown=()=>out.textContent='down';hold.onpointerup=()=>out.textContent='up';document.querySelector('[name=message]').oninput=e=>out.textContent='typed:'+e.target.value;setTimeout(()=>late.textContent='arrived',1500);
</script>'''

@pytest.fixture
def local_page():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(HTML)
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown();server.server_close()

@pytest.fixture
def browser(tmp_path, monkeypatch):
    executable=os.environ.get('TEST_CHROME','/usr/bin/google-chrome')
    if not Path(executable).is_file():pytest.skip('set TEST_CHROME to an installed Chromium executable')
    monkeypatch.setenv('DOT_MCP_BROWSER_EXECUTABLE', executable)
    b=LocalBrowser(tmp_path, 'http://testserver', lambda tid,path,name: {'task_id':tid,'path':path})
    yield b
    b.call('close')
    b.loop.call_soon_threadsafe(b.loop.stop)
    b.thread.join(timeout=3)


def find(snapshot,label):
    return next(x['ref'] for x in snapshot['elements'] if x['label']==label)


def test_snapshot_reports_element_state(browser,local_page):
    browser.call('open',url=local_page)
    snap=browser.call('snapshot')
    by_label={x['label']:x for x in snap['elements']}
    assert by_label['Off']['disabled'] is True
    assert by_label['Hold here']['disabled'] is False
    box=next(x for x in snap['elements'] if x['tag']=='input' and x.get('type')=='checkbox')
    assert box['checked'] is False
    sel=next(x for x in snap['elements'] if x['tag']=='select')
    assert sel['selected_option']=='Alpha'
    browser.call('click',ref=box['ref'])
    snap2=browser.call('snapshot')
    box2=next(x for x in snap2['elements'] if x['tag']=='input' and x.get('type')=='checkbox')
    assert box2['checked'] is True


def test_select_hover_wait_back_reload(browser,local_page):
    browser.call('open',url=local_page)
    snap=browser.call('snapshot')
    sel=next(x for x in snap['elements'] if x['tag']=='select')
    picked=browser.call('select',ref=sel['ref'],option='Beta')
    assert picked['selected']=='Beta'
    snap=browser.call('snapshot')
    sel2=next(x for x in snap['elements'] if x['tag']=='select')
    assert sel2['selected_option']=='Beta'
    with pytest.raises(BrowserError,match='no such option'):
        browser.call('select',ref=sel2['ref'],option='Gamma')
    msg=find(browser.call('snapshot'),'message')
    with pytest.raises(BrowserError,match='select dropdown'):
        browser.call('select',ref=msg,option='Beta')
    hovered=browser.call('hover',ref=find(browser.call('snapshot'),'Hold here'))
    assert 'fresh snapshot' in hovered['note']
    waited=browser.call('wait',text='arrived',timeout=10)
    assert 'arrived' in waited['text']
    with pytest.raises(BrowserError,match='timed out after'):
        browser.call('wait',text='never-comes-xyz',timeout=1)
    browser.call('navigate',url=local_page+'#second')
    back=browser.call('back')
    assert back['url']==local_page or back['url'].rstrip('/')==local_page.rstrip('/')
    reloaded=browser.call('reload')
    assert 'fresh snapshot' in reloaded['note']


def test_tools_profile_secrets_screenshot(browser,local_page):
    opened=browser.call('open',url=local_page)
    assert opened['live_view_url'] != opened['live_control_url']
    snap=browser.call('snapshot')
    browser.call('type',ref=find(snap,'message'),text='hello')
    assert 'typed:hello' in browser.call('snapshot')['text']
    snap=browser.call('snapshot')
    with pytest.raises(BrowserError,match='secret field'):
        browser.call('type',ref=find(snap,'password (manual entry only)'),text='secret')
    shot=browser.call('screenshot',task_id='task-test')
    assert Path(shot['path']).exists()
    assert shot['artifact']['task_id']=='task-test'
    assert browser.profile.is_relative_to(browser.root)
    with pytest.raises(BrowserError): browser.call('navigate',url='file:///etc/passwd')
    token=browser.tokens['view']
    browser.call('close')
    assert browser.authorize(token) is None


def receive_type(ws,kind):
    for _ in range(100):
        msg=ws.receive_json()
        if msg['type']=='frame':ws.send_json({'action':'frame_ack','sequence':msg['sequence']})
        if msg['type']==kind:return msg
    raise AssertionError('message not found')


def test_live_auth_view_control_hold_type_scroll(browser,local_page):
    browser.call('open',url=local_page)
    app=Starlette(routes=routes(lambda create=False:browser,lambda:'http://testserver',lambda token:token=='fixture-owner'))
    with TestClient(app) as client:
        assert client.get('/live').headers['referrer-policy']=='no-referrer'
        assert browser.tokens['control'] not in client.get('/live').text
        with pytest.raises(Exception):
            with client.websocket_connect('/live/ws',headers={'origin':'http://evil.test'}):pass
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/live/ws',headers={'origin':'http://testserver'}) as denied:
                denied.send_json({'token':browser.tokens['control']});denied.receive_json()
        with client.websocket_connect('/live/ws',headers={'origin':'http://testserver'}) as view:
            view.send_json({'token':browser.tokens['view'],'credential':'fixture-owner'})
            assert receive_type(view,'ready')['role']=='view'
            view.send_json({'action':'claim'})
            assert 'cannot control' in receive_type(view,'error')['message']
        with client.websocket_connect('/live/ws',headers={'origin':'http://testserver'}) as ws:
            ws.send_json({'token':browser.tokens['control'],'credential':'fixture-owner'});receive_type(ws,'ready')
            ws.send_json({'action':'claim'});receive_type(ws,'control')
            with pytest.raises(BrowserError,match='human control'):
                browser.call('navigate',url=local_page)
            async def rect(selector):return await browser.page.locator(selector).bounding_box()
            r=asyncio.run_coroutine_threadsafe(rect('#hold'),browser.loop).result()
            ws.send_json({'action':'mouse','kind':'down','x':r['x']+20,'y':r['y']+20});receive_type(ws,'input_ack')
            time.sleep(0.2)
            assert 'down' in browser.call('snapshot')['text']
            ws.send_json({'action':'mouse','kind':'up','x':r['x']+20,'y':r['y']+20});receive_type(ws,'input_ack')
            assert 'up' in browser.call('snapshot')['text']
            r=asyncio.run_coroutine_threadsafe(rect('[name=message]'),browser.loop).result()
            for kind in ('down','up'):
                ws.send_json({'action':'mouse','kind':kind,'x':r['x']+20,'y':r['y']+10});receive_type(ws,'input_ack')
            ws.send_json({'action':'text','text':'human'});receive_type(ws,'input_ack')
            assert 'typed:human' in browser.call('snapshot')['text']
            ws.send_json({'action':'wheel','y':500,'x':0});receive_type(ws,'input_ack')
            time.sleep(0.2)
            scroll=asyncio.run_coroutine_threadsafe(browser.page.evaluate('window.scrollY'),browser.loop).result()
            assert scroll>0
            ws.send_json({'action':'release'});receive_type(ws,'control')
        browser.call('navigate',url=local_page)


def test_frames_measured(browser,local_page):
    browser.call('open',url=local_page)
    before=browser.frames;start=time.monotonic();time.sleep(3)
    fps=(browser.frames-before)/(time.monotonic()-start)
    print(f'CDP_CAPTURE_MEASURED_FPS={fps:.2f}')
    assert fps>0


def test_stale_dom_reference_and_expiry(browser,local_page):
    browser.call('open',url=local_page)
    snap=browser.call('snapshot');ref=find(snap,'message')
    asyncio.run_coroutine_threadsafe(browser.page.evaluate("document.querySelector('[name=message]').remove()"),browser.loop).result()
    with pytest.raises(BrowserError,match='target changed'):browser.call('type',ref=ref,text='wrong target')
    browser.expires=time.time()-1
    assert browser.authorize(browser.tokens['control']) is None


def test_fallback_frames(browser,local_page,monkeypatch):
    from playwright.async_api import BrowserContext
    async def fail(*args,**kwargs):raise RuntimeError('simulated CDP unavailable')
    monkeypatch.setattr(BrowserContext,'new_cdp_session',fail)
    opened=browser.call('open',url=local_page)
    assert opened['stream_mode']=='screenshot-fallback-5fps'
    time.sleep(.8)
    assert browser.frames>0


def test_stream_config_bounds(tmp_path,monkeypatch):
    monkeypatch.setenv('DOT_MCP_STREAM_QUALITY','99');monkeypatch.setenv('DOT_MCP_STREAM_WIDTH','10')
    b=LocalBrowser(tmp_path,'http://testserver')
    assert b.stream_quality==85 and b.stream_width==640
    b.loop.call_soon_threadsafe(b.loop.stop);b.thread.join(timeout=3)


def test_live_proxy_warning_cookie_never_bypasses_auth(browser,local_page):
    from starlette.websockets import WebSocketDisconnect
    browser.base_url='https://fixture-abcd.tunnl.gg'
    browser.call('open',url=local_page)
    app=Starlette(routes=routes(lambda create=False:browser,lambda:'https://fixture-abcd.tunnl.gg',lambda token:token=='fixture-owner'))
    with TestClient(app) as client:
        headers={'origin':'https://fixture-abcd.tunnl.gg','host':'localhost:33041','x-forwarded-host':'evil.test','x-forwarded-proto':'http','cookie':'tunnl-warning=accepted'}
        assert client.get('/live',headers=headers).status_code==200
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/live/ws',headers=headers) as ws:
                ws.send_json({'token':browser.tokens['view']});ws.receive_json()
        with client.websocket_connect('/live/ws',headers=headers) as ws:
            ws.send_json({'token':browser.tokens['view'],'credential':'fixture-owner'})
            assert receive_type(ws,'ready')['role']=='view'
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/live/ws',headers=dict(headers,origin='https://evil.test')):pass


def test_installed_executable_selection(monkeypatch):
    from dot_mcp.browser import browser_executable
    monkeypatch.delenv('DOT_MCP_BROWSER_EXECUTABLE',raising=False)
    monkeypatch.setattr('dot_mcp.browser.shutil.which',lambda name:'/home/fixture/.local/bin/google-chrome' if name=='google-chrome' else None)
    assert browser_executable()=='/home/fixture/.local/bin/google-chrome'
    monkeypatch.setenv('DOT_MCP_BROWSER_EXECUTABLE','/explicit/chromium')
    assert browser_executable()=='/explicit/chromium'
    monkeypatch.delenv('DOT_MCP_BROWSER_EXECUTABLE')
    monkeypatch.setattr('dot_mcp.browser.shutil.which',lambda name:None)
    assert browser_executable() is None


def test_password_free_view_opt_in_control_still_requires_owner(browser,local_page):
    from starlette.websockets import WebSocketDisconnect
    browser.call('open',url=local_page)
    old=browser.tokens['view']
    sharing=browser.call('share_view',enabled=True)
    assert sharing['password_free_view']
    assert browser.authorize(old) is None
    app=Starlette(routes=routes(lambda create=False:browser,lambda:'http://testserver',lambda token:token=='fixture-owner'))
    with TestClient(app) as client:
        with client.websocket_connect('/live/ws',headers={'origin':'http://testserver'}) as view:
            view.send_json({'token':browser.tokens['view']})
            assert receive_type(view,'ready')['role']=='view'
            view.send_json({'action':'claim'})
            assert 'cannot control' in receive_type(view,'error')['message']
            view.send_json({'action':'text','text':'not allowed'})
            assert 'cannot control' in receive_type(view,'error')['message']
        for credential in ('','wrong'):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect('/live/ws',headers={'origin':'http://testserver'}) as control:
                    control.send_json({'token':browser.tokens['control'],'credential':credential});control.receive_json()
        with client.websocket_connect('/live/ws',headers={'origin':'http://testserver'}) as control:
            control.send_json({'token':browser.tokens['control'],'credential':'fixture-owner'})
            assert receive_type(control,'ready')['role']=='control'
        old=browser.tokens['view']
        browser.call('share_view',enabled=False)
        assert browser.authorize(old) is None
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/live/ws',headers={'origin':'http://testserver'}) as denied:
                denied.send_json({'token':browser.tokens['view']});denied.receive_json()


def test_live_ready_reports_domain(browser,local_page):
    browser.call('open',url=local_page)
    app=Starlette(routes=routes(lambda create=False:browser,lambda:'http://testserver',lambda token:token=='fixture-owner'))
    with TestClient(app) as client:
        with client.websocket_connect('/live/ws',headers={'origin':'http://testserver'}) as view:
            view.send_json({'token':browser.tokens['view'],'credential':'fixture-owner'})
            ready=receive_type(view,'ready')
            assert ready['domain']=='127.0.0.1'


def test_revoke_disconnects_connected_password_free_view(browser,local_page):
    from starlette.websockets import WebSocketDisconnect
    browser.call('open',url=local_page,share_view=True)
    app=Starlette(routes=routes(lambda create=False:browser,lambda:'http://testserver',lambda token:token=='fixture-owner'))
    with TestClient(app) as client:
        with client.websocket_connect('/live/ws',headers={'origin':'http://testserver'}) as view:
            view.send_json({'token':browser.tokens['view']});receive_type(view,'ready')
            browser.call('share_view',enabled=False)
            view.send_json({'action':'frame_ack','sequence':0})
            with pytest.raises(WebSocketDisconnect):
                for _ in range(50):view.receive_json()
