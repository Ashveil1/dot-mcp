"""Production transport leak check with fixture-only credentials."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import pytest
pytest.importorskip('cryptography')
pytest.importorskip('playwright')
from test_browser import local_page
from test_vault import PASSPHRASE,SECRET
from dot_mcp.vault import LocalVault


def test_real_unlocked_server_fill_outputs_and_logs(tmp_path,local_page):
    chrome=os.environ.get('TEST_CHROME','/usr/bin/google-chrome')
    if not Path(chrome).exists():pytest.skip('installed Chromium required')
    data=tmp_path/'data'
    v=LocalVault(data);v.unlock(PASSPHRASE,create=True)
    v.set_entry('fixture',local_page,{'password':SECRET},allow_loopback=True);v.close()
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    # Fixture constants in test bootstrap, never a production unlock endpoint.
    wrapper=tmp_path/'fixture_boot.py'
    wrapper.write_text("import os,runpy\nfrom dot_mcp import vault\nv=vault.LocalVault(os.environ['DOT_MCP_DATA'])\nv.unlock("+repr(PASSPHRASE)+")\nvault.ACTIVE_VAULT=v\nrunpy.run_module('dot_mcp.server',run_name='__main__')\n")
    env=dict(os.environ,HOME=str(tmp_path),DOT_MCP_DATA=str(data),TMPDIR=str(tmp_path/'tmp'),MCP_HOST='127.0.0.1',MCP_PORT=str(port),MCP_PUBLIC_HOST='',MCP_PATH_SECRET='fixture-owner',DOT_MCP_BROWSER_EXECUTABLE=chrome,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'src'))
    logs=tmp_path/'server.log'
    with logs.open('wb') as log:child=subprocess.Popen([sys.executable,str(wrapper)],env=env,stdout=log,stderr=log)
    outputs=[]
    async def check():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        try:import httpx2 as httpx
        except ImportError:import httpx
        async with httpx.AsyncClient(headers={'Authorization':'Bearer fixture-owner'}) as http:
            async with streamable_http_client(f'http://127.0.0.1:{port}/mcp/',http_client=http) as streams:
                async with ClientSession(streams[0],streams[1]) as session:
                    await session.initialize()
                    async def call(name,args):
                        result=await session.call_tool(name,args)
                        text='\n'.join(x.text for x in result.content if hasattr(x,'text'));outputs.append(text)
                        assert SECRET not in text
                        return result,text
                    _,text=await call('vault_list',{});assert json.loads(text)['names']==['fixture']
                    opened,raw=await call('browser_open',{'url':local_page});assert not getattr(opened,'is_error',getattr(opened,'isError',False)),raw
                    res,text=await call('browser_snapshot',{});assert not getattr(res,'is_error',getattr(res,'isError',False)),text
                    ref=next(e['ref'] for e in json.loads(text)['elements'] if e['label']=='message')
                    _,text=await call('vault_fill',{'name':'fixture','key':'password','ref':ref});assert json.loads(text)['filled']
                    for action in ('browser_snapshot','browser_screenshot'):
                        result,_=await call(action,{})
                        assert getattr(result,'is_error',getattr(result,'isError',False))
                    await call('browser_close',{})
                    await call('vault_lock',{})
                    result,_=await call('vault_list',{})
                    assert getattr(result,'is_error',getattr(result,'isError',False))
    try:
        for _ in range(100):
            try:urlopen(f'http://127.0.0.1:{port}/health',timeout=.2);break
            except OSError:
                if child.poll() is not None:raise AssertionError(logs.read_text())
                time.sleep(.05)
        else:raise AssertionError('server unavailable')
        asyncio.run(check())
    finally:
        child.terminate()
        try:child.wait(timeout=5)
        except subprocess.TimeoutExpired:child.kill();child.wait()
    assert SECRET not in logs.read_text()
    assert SECRET not in (data/'audit.jsonl').read_text()
    assert not list((data/'browser/screenshots').glob('*.png'))
