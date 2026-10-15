"""Mocked quick-tunnel discovery and lifecycle. No provider connections."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from dot_mcp import cli, orchestrator as ui
from dot_mcp.transport_security import transport_security


def test_dev_flow_sets_discovered_host_before_one_boot(tmp_path, monkeypatch):
    calls = []
    ctx = ui.StartCtx(33041, str(tmp_path), 'fixture', 'python',
                      {'MCP_PUBLIC_HOST': 'stale.test'}, tmp_path, dev=True)
    def tunnel(*args):
        calls.append('dev-tunnel')
        return 'https://fixture.trycloudflare.com'
    monkeypatch.setattr(ui, '_start_dev_tunnel', tunnel)
    monkeypatch.setattr(ui, '_start_tunnl', lambda *a: pytest.fail('production tunnel in dev'))
    monkeypatch.setattr(ui, '_ensure_tunnl_key', lambda *a: pytest.fail('SSH key in dev'))
    def boot(py, env, *args):
        calls.append('boot')
        assert env['MCP_PUBLIC_HOST'] == 'fixture.trycloudflare.com'
        ts = transport_security(env['MCP_PUBLIC_HOST'])
        assert ts.enable_dns_rebinding_protection
        assert 'fixture.trycloudflare.com' in ts.allowed_hosts
        assert 'https://fixture.trycloudflare.com' in ts.allowed_origins
        assert '*' not in ts.allowed_hosts
        return SimpleNamespace(pid=123)
    monkeypatch.setattr(ui, '_boot_server', boot)
    monkeypatch.setattr(ui, '_wait_health', lambda *a: True)
    monkeypatch.setattr(ui, '_tunnel_alive', lambda *a: True)
    def verify(url, secret):
        calls.append('verify')
        assert url == 'https://fixture.trycloudflare.com/mcp/'
        assert secret == 'fixture'
        return True
    monkeypatch.setattr(ui, '_public_init_ok', verify)
    result = ui.plain_flow(ctx, tmp_path, SimpleNamespace(print=lambda *a: None), True)
    assert result.ok and calls == ['dev-tunnel', 'boot', 'verify']
    output = json.loads(ui.json_result(True, ctx, result, {}))
    assert output['mode'] == 'dev' and output['unstable'] is True
    assert output['tunnel_provider'] == 'cloudflare-quick'
    assert output['issuer'] == 'https://fixture.trycloudflare.com'
    ctx.dev = False
    assert json.loads(ui.json_result(True, ctx, result, {}))['mode'] == 'local'


def test_missing_cloudflared_before_secrets_or_install(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, 'which', lambda *a: None)
    monkeypatch.setattr(cli, 'get_secret', lambda: pytest.fail('should fail before credentials'))
    monkeypatch.setattr(cli.subprocess, 'run', lambda *a, **k: pytest.fail('no subprocess/install'))
    assert cli._cmd_start(argparse.Namespace(port=33041, data_dir=str(tmp_path),
                           json=True, vault=False, dev=True)) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err.strip() == 'not installed: cloudflared'


@pytest.mark.parametrize('event', ['success', 'exit', 'timeout', 'interrupt'])
def test_provider_discovery_and_cleanup(tmp_path, monkeypatch, event):
    monkeypatch.setattr(ui.shutil, 'which', lambda *a: '/fixture/cloudflared')
    monkeypatch.setenv('TUNNEL_TOKEN', 'do-not-inherit-named-tunnel-token')
    stops = []
    kills = []
    records = []
    monkeypatch.setattr(ui.lifecycle, 'stop', lambda path: stops.append(path))
    monkeypatch.setattr(ui.lifecycle, 'record', lambda path, pid: records.append((path, pid)))
    monkeypatch.setattr(ui, '_kill_proc', lambda proc: kills.append(proc.pid))
    clock = iter([0, 0, 100])
    monkeypatch.setattr(ui.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(ui.time, 'sleep', lambda *a: None)
    proc = SimpleNamespace(pid=123, poll=lambda: 1 if event == 'exit' else None)
    def popen(command, **kwargs):
        assert command[0:2] == ['/fixture/cloudflared', 'tunnel']
        config = Path(command[command.index('--config') + 1])
        assert config.read_text() == '{}\n' and config.stat().st_mode & 0o777 == 0o600
        assert '--no-autoupdate' in command
        assert command[-2:] == ['--url', 'http://127.0.0.1:33041']
        assert 'TUNNEL_TOKEN' not in kwargs['env']
        if event == 'interrupt':
            raise KeyboardInterrupt()
        kwargs['stdout'].write(b'provider banner https://fixture.trycloudflare.com\n')
        kwargs['stdout'].flush()
        if event == 'timeout':
            (tmp_path / 'tunnel.log').write_text('no URL yet')
        return proc
    monkeypatch.setattr(ui.subprocess, 'Popen', popen)
    if event == 'success':
        assert ui._start_dev_tunnel(tmp_path, 33041) == 'https://fixture.trycloudflare.com'
        assert not kills
    else:
        with pytest.raises(KeyboardInterrupt if event == 'interrupt' else RuntimeError):
            ui._start_dev_tunnel(tmp_path, 33041)
        assert len(stops) == 2
        if event != 'interrupt':
            assert kills == [123]
    assert not list((tmp_path / 'logs').glob('quick-tunnel-*.yml'))
    assert (tmp_path / 'tunnel.log').stat().st_mode & 0o777 == 0o600
    if event != 'interrupt':
        assert records == [(tmp_path / 'logs' / 'tunnel.json', 123)]


@pytest.mark.parametrize('url', ['https://evil.test/fake.trycloudflare.com',
                              'https://fixture.trycloudflare.com.evil.test'])
def test_dev_url_lookalikes_not_discovered(url):
    assert ui._DEV_URL.search(url) is None


def test_dev_option_routes_to_start(monkeypatch):
    monkeypatch.setattr(cli, 'cmd_start', lambda args: 27 if args.dev else 28)
    assert cli.main(['start', '--dev']) == 27
    assert cli.main(['start']) == 28


def test_dev_vault_uses_same_provider(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.shutil, 'which', lambda *a: '/fixture/cloudflared')
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr(cli.ui, '_start_dev_tunnel', lambda *a: 'https://fixture.trycloudflare.com')
    monkeypatch.setattr(cli.ui, '_start_tunnl', lambda *a: pytest.fail('wrong provider'))
    monkeypatch.setattr(cli.ui, '_kill_tunnel', lambda *a: None)
    for name in ('MCP_PUBLIC_HOST', 'MCP_PORT', 'MCP_HOST'):
        monkeypatch.setenv(name, 'fixture-previous')
    def serve(argv):
        import os
        assert os.environ['MCP_PUBLIC_HOST'] == 'fixture.trycloudflare.com'
        return 0
    monkeypatch.setattr('dot_mcp.vault.main', serve)
    assert cli._cmd_start(argparse.Namespace(port=33041, data_dir=str(tmp_path),
                          json=False, vault=True, dev=True)) == 0

@pytest.mark.parametrize('event', ['health', 'verify', 'tunnel-exit', 'interrupt'])
def test_failed_dev_start_stops_both_owned_children(tmp_path, monkeypatch, event):
    ctx = ui.StartCtx(33041, str(tmp_path), 'fixture', 'python', {}, tmp_path, dev=True)
    stops = []
    monkeypatch.setattr(ui.lifecycle, 'stop', lambda path: stops.append(path.name))
    def tunnel(*args):
        if event == 'interrupt':
            raise KeyboardInterrupt()
        return 'https://fixture.trycloudflare.com'
    monkeypatch.setattr(ui, '_start_dev_tunnel', tunnel)
    monkeypatch.setattr(ui, '_boot_server', lambda *a: SimpleNamespace(pid=123))
    monkeypatch.setattr(ui, '_wait_health', lambda *a: event != 'health')
    monkeypatch.setattr(ui, '_tunnel_alive', lambda *a: event != 'tunnel-exit')
    monkeypatch.setattr(ui, '_public_init_ok', lambda *a: event != 'verify')
    monkeypatch.setattr(ui, '_kill_proc', lambda *a: None)
    if event == 'interrupt':
        with pytest.raises(KeyboardInterrupt):
            ui.plain_flow(ctx, tmp_path, SimpleNamespace(print=lambda *a: None), True)
    else:
        assert not ui.plain_flow(ctx, tmp_path, SimpleNamespace(print=lambda *a: None), True).ok
    assert stops == ['server.json', 'tunnel.json']
