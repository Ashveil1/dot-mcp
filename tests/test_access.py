"""Security boundaries: real credentials, no provider/network accounts."""
import asyncio
from types import SimpleNamespace
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient
from dot_mcp import server as srv, orchestrator as ui

@pytest.fixture
def access(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, 'OAUTH_FILE', tmp_path / 'oauth.json')
    srv._oauth_ensure()
    app = Starlette(routes=[Route('/access', srv._access_req, methods=['GET', 'POST'])])
    with TestClient(app) as client:
        yield client


def test_separate_keys_revoke_next_dispatch(access, monkeypatch):
    read = srv._access_create('look only', srv.READ_SCOPE)
    full = srv._access_create('do things', srv.OAUTH_SCOPE)
    seen = []
    async def execute(*args, **kwargs):
        seen.append(args[0])
        return 'ok'
    monkeypatch.setattr(srv, '_original_call_tool', execute)
    async def call(token, name, args):
        context = srv._ACCESS.set({'token': token})
        try:
            return await srv._safe_call_tool(name, args)
        finally:
            srv._ACCESS.reset(context)
    assert asyncio.run(call(read, 'list_dir', {})) == 'ok'
    for name, args in [('run_command', {'command':'echo denied'}),
                       ('write_file', {'path':'a','content':'b'}),
                       ('browser_open', {}), ('vault_list', {}),
                       ('add_schedule', {'name':'job','command':'echo denied'})]:
        with pytest.raises(srv.SafeToolError):
            asyncio.run(call(read, name, args))
    assert seen == ['list_dir']
    assert asyncio.run(call(full, 'run_command', {'command':'echo accepted'})) == 'ok'
    cid = srv._jwt_verify(full)['client_id']
    srv._access_revoke(cid)
    assert not srv._jwt_verify(full)
    with pytest.raises(srv.SafeToolError):
        asyncio.run(call(full, 'run_command', {'command':'echo denied'}))
    assert seen == ['list_dir','run_command']
    assert srv._jwt_verify(read)  # another client remains valid
    assert not srv._full_credential(read)  # cannot unlock /live control


def test_owner_only_management_escaping_and_revoke(access):
    assert access.get('/access').status_code == 200
    assert access.post('/access', data={'owner_secret':'wrong'}).status_code == 401
    read = srv._access_create('<script>bad</script>', srv.READ_SCOPE)
    assert access.post('/access', data={'owner_secret':read}).status_code == 401
    form = {'owner_secret':srv.SECRET}
    assert access.post('/access', data=form, headers={'Origin':'https://evil.test'}).status_code == 403
    page = access.post('/access', data=form)
    assert page.headers['cache-control'] == 'no-store'
    assert '<script>bad</script>' not in page.text
    assert '&lt;script&gt;bad&lt;/script&gt;' in page.text
    cid = srv._jwt_verify(read)['client_id']
    assert access.post('/access', data={**form,'action':'revoke','client_id':cid}).status_code == 200
    assert not srv._credential(read)
    assert access.post('/access', data={**form,'action':'create','name':'test','scope':'invalid'}).status_code == 400


def test_revoke_removes_pending_codes_refresh_and_old_tokens(access):
    key = srv._access_create('fixture', srv.OAUTH_SCOPE)
    cid = srv._jwt_verify(key)['client_id']
    def seed(st):
        st['codes']={'code':{'client_id':cid},'other':{'client_id':'other'}}
        st['refresh']={'ref':{'client_id':cid}}
        return st, None
    srv.mutate_json(srv.OAUTH_FILE, {}, seed)
    srv._access_revoke(cid)
    st=srv._oauth_state()
    assert st['codes']=={'other':{'client_id':'other'}} and not st['refresh']
    # Reapproval must never resurrect old credentials.
    st['clients'][cid]['revoked']=False
    srv._oauth_save(st)
    assert not srv._jwt_verify(key)


def test_local_start_never_discovers_tunnel(tmp_path, monkeypatch):
    ctx=ui.StartCtx(33041,str(tmp_path),'fixture','python',{'MCP_PUBLIC_HOST':'stale.test'},tmp_path)
    monkeypatch.setattr(ui,'_start_tunnl',lambda *a:pytest.fail('tunnel not opted in'))
    monkeypatch.setattr(ui,'_start_dev_tunnel',lambda *a:pytest.fail('dev not opted in'))
    monkeypatch.setattr(ui,'_ensure_tunnl_key',lambda *a:pytest.fail('key not needed'))
    stopped=[]
    monkeypatch.setattr(ui,'_kill_tunnel',lambda *a:stopped.append(True))
    def boot(py,env,*a):
        assert stopped == [True]
        assert 'MCP_PUBLIC_HOST' not in env
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(ui,'_boot_server',boot)
    monkeypatch.setattr(ui,'_wait_health',lambda *a:True)
    result=ui.plain_flow(ctx,tmp_path,SimpleNamespace(print=lambda *a:None),True)
    assert result.ok and result.public_url=='http://127.0.0.1:33041/mcp/'


def test_consent_defaults_readonly_and_scope_validation(access):
    from dot_mcp.web_ui import authorize
    page=authorize('fixture',srv.OAUTH_SCOPE,'')
    assert 'value="dots:read" selected' in page
    assert 'value="dots:full"' in page
