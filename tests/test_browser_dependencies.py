"""Dependency checks use fixtures only, with no installer/network/browser launch."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest
from dot_mcp import browser as module


@pytest.fixture
def local_browser(tmp_path):
    b=module.LocalBrowser(tmp_path,'http://fixture.test')
    yield b
    b.call('close')
    b.loop.call_soon_threadsafe(b.loop.stop)
    b.thread.join(timeout=3)


def test_missing_packages_result_no_start_or_install(monkeypatch,local_browser):
    monkeypatch.setattr(module.importlib.util,'find_spec',lambda name:None)
    monkeypatch.setattr(module,'browser_executable',lambda:None)
    result=local_browser.call('open')
    assert result['ok'] is False
    assert result['error']=='not_installed'
    assert result['missing']==['playwright','websockets']
    assert result['message']=='not installed: playwright, websockets'
    assert 'user approval' in result['next_action']
    assert local_browser.playwright is None
    assert not local_browser.root.exists()


def test_explicit_missing_or_nonexecutable_never_falls_back(tmp_path,monkeypatch):
    monkeypatch.setattr(module.importlib.util,'find_spec',lambda name:object())
    path=tmp_path/'chromium'
    monkeypatch.setenv('DOT_MCP_BROWSER_EXECUTABLE',str(path))
    assert module.browser_dependencies()['missing']==['configured Chrome/Chromium executable']
    path.write_text('fixture');path.chmod(0o600)
    assert module.browser_dependencies()['missing']==['configured Chrome/Chromium executable']
    path.chmod(0o700)
    assert module.browser_dependencies()['missing']==[]


def fake_playwright(monkeypatch,managed):
    class Runtime:
        chromium=SimpleNamespace(executable_path=str(managed))
        stopped=False
        async def stop(self):self.stopped=True
    runtime=Runtime()
    class Manager:
        async def start(self):return runtime
    monkeypatch.setitem(sys.modules,'playwright.async_api',SimpleNamespace(async_playwright=Manager))
    monkeypatch.setattr(module.importlib.util,'find_spec',lambda name:object())
    monkeypatch.setattr(module,'browser_executable',lambda:None)
    return runtime


def test_missing_managed_browser_clear_result_and_cleanup(tmp_path,monkeypatch,local_browser):
    runtime=fake_playwright(monkeypatch,tmp_path/'not-installed')
    result=local_browser.call('open')
    assert result['missing']==['Chrome/Chromium (PATH or Playwright-managed)']
    assert result['message'].startswith('not installed: Chrome/Chromium')
    assert runtime.stopped
    assert local_browser.playwright is None


def test_managed_fallback_and_runtime_failure_not_misdiagnosed(tmp_path,monkeypatch,local_browser):
    managed=tmp_path/'managed-chromium';managed.write_text('fixture');managed.chmod(0o700)
    runtime=fake_playwright(monkeypatch,managed)
    async def launch(*args,**kwargs):
        assert kwargs['executable_path'] is None
        assert kwargs['chromium_sandbox'] is True
        raise RuntimeError('fixture: installed browser missing system library')
    runtime.chromium.launch_persistent_context=launch
    with pytest.raises(module.BrowserError,match='missing system library'):
        local_browser.call('open')
    assert runtime.stopped
    assert local_browser.browser_tmp is None


def test_dependency_recheck_not_cached(tmp_path,monkeypatch):
    present=set()
    monkeypatch.setattr(module.importlib.util,'find_spec',lambda name:object() if name in present else None)
    monkeypatch.setattr(module,'browser_executable',lambda:None)
    assert module.browser_dependencies()['missing']==['playwright','websockets']
    present.update(('playwright','websockets'))
    assert module.browser_dependencies()['missing']==[]


def test_tool_wrapper_preserves_actionable_result(monkeypatch):
    from dot_mcp import server
    result=module.not_installed(['playwright'])
    fake=SimpleNamespace(call=lambda *a,**kw:result)
    monkeypatch.setattr(server,'_browser',lambda *a,**kw:fake)
    assert server.browser_open()==result


@pytest.mark.parametrize('missing',['playwright','websockets'])
def test_single_missing_package_is_named(monkeypatch,local_browser,missing):
    monkeypatch.setattr(module.importlib.util,'find_spec',lambda name:None if name==missing else object())
    monkeypatch.setattr(module,'browser_executable',lambda:None)
    result=local_browser.call('open')
    assert result['missing']==[missing]
    assert result['message']=='not installed: '+missing


def test_doctor_reports_same_missing_dependencies(monkeypatch,capsys):
    import argparse,json
    from dot_mcp import cli
    monkeypatch.setattr(module.importlib.util,'find_spec',lambda name:None)
    monkeypatch.setattr(module,'browser_executable',lambda:None)
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**kw:(_ for _ in ()).throw(OSError('offline')))
    assert cli.cmd_doctor(argparse.Namespace(port=1,json=True))==0
    assert json.loads(capsys.readouterr().out)['optional']['browser_missing']==['playwright','websockets']
