from types import SimpleNamespace
import pytest
from dot_mcp import orchestrator as ui


@pytest.fixture
def flow(tmp_path,monkeypatch):
    calls=[]
    ctx=ui.StartCtx(33041,str(tmp_path),'fixture','python',{'MCP_PUBLIC_HOST':'stale.test'},tmp_path)
    monkeypatch.setattr(ui,'_ensure_tunnl_key',lambda *a:tmp_path/'key')
    def tunnel(*a):calls.append('tunnel');return 'https://fixture-abc.tunnl.gg'
    monkeypatch.setattr(ui,'_start_tunnl',tunnel)
    def boot(py,env,*a):
        calls.append('boot');assert env['MCP_PUBLIC_HOST']=='fixture-abc.tunnl.gg'
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(ui,'_boot_server',boot)
    monkeypatch.setattr(ui,'_wait_health',lambda *a:True)
    monkeypatch.setattr(ui,'_tunnel_alive',lambda *a:True)
    monkeypatch.setattr(ui,'_public_init_ok',lambda *a:True)
    monkeypatch.setattr(ui,'_kill_proc',lambda *a:calls.append('kill-server'))
    monkeypatch.setattr(ui.lifecycle,'stop',lambda *a:calls.append('owned-stop'))
    monkeypatch.setattr(ui,'_kill_tunnel',lambda *a:calls.append('kill-tunnel'))
    return ctx,calls,SimpleNamespace(print=lambda *a:None)


def test_tunnel_first_single_boot(flow):
    ctx,calls,console=flow
    result=ui.plain_flow(ctx,ctx.repo,console,leave_running=True)
    assert result.ok and result.host=='fixture-abc.tunnl.gg'
    assert calls==['tunnel','boot']
    assert ctx.env['MCP_PUBLIC_HOST']==result.host


@pytest.mark.parametrize('stage',['tunnel','boot','health','verify','interrupted'])
def test_failed_start_cleans_owned_children(flow,monkeypatch,stage):
    ctx,calls,console=flow
    def fail(*a):raise RuntimeError('fixture')
    if stage=='tunnel':monkeypatch.setattr(ui,'_start_tunnl',fail)
    elif stage=='boot':monkeypatch.setattr(ui,'_boot_server',fail)
    elif stage=='health':monkeypatch.setattr(ui,'_wait_health',lambda *a:False)
    elif stage=='verify':monkeypatch.setattr(ui,'_public_init_ok',lambda *a:False)
    else:
        def interrupt(*a):raise KeyboardInterrupt()
        monkeypatch.setattr(ui,'_boot_server',interrupt)
    if stage in ('tunnel','boot','interrupted'):
        with pytest.raises((RuntimeError,KeyboardInterrupt)):ui.plain_flow(ctx,ctx.repo,console,True)
    else:assert not ui.plain_flow(ctx,ctx.repo,console,True).ok
    assert calls[-2:]==['owned-stop','kill-tunnel']
