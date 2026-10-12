import argparse
import json
from pathlib import Path
import pytest
from dot_mcp import tunnel


def test_stable_ssh_command_no_shell_or_secrets(tmp_path):
    command=tunnel.ssh_command(33041,tmp_path/'identity',tmp_path/'known_hosts')
    assert command[:3]==['ssh','-t','-i']
    assert 'IdentitiesOnly=yes' in command
    assert 'ServerAliveInterval=30' in command
    assert 'ExitOnForwardFailure=yes' in command
    assert command[-3:]==['-R','80:localhost:33041','stable@proxy.tunnl.gg']
    with pytest.raises(ValueError):tunnel.ssh_command(0,'key','hosts')
    with pytest.raises(ValueError):tunnel.ssh_command(65536,'key','hosts')


def test_only_url_stdout_mock_provider(tmp_path,monkeypatch,capsys):
    identity=tmp_path/'key';identity.write_text('fixture-not-real-key')
    monkeypatch.setattr(tunnel.shutil,'which',lambda name:'/usr/bin/'+name)
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,*args):return b'{"ok":true,"tools":45}'
    monkeypatch.setattr(tunnel.urllib.request,'urlopen',lambda *args,**kwargs:Response())
    class Process:
        def __init__(self,command,**kwargs):
            assert isinstance(command,list)
            kwargs['stdout'].write(b'provider banner PRIVATE-NOT-FOR-STDOUT https://fixture-abcd.tunnl.gg\n');kwargs['stdout'].flush()
            self.done=False
        def poll(self):return 0 if self.done else None
        def wait(self,**kwargs):self.done=True;return 0
    monkeypatch.setattr(tunnel.subprocess,'Popen',Process)
    assert tunnel.run(argparse.Namespace(port=33041,state_dir=str(tmp_path/'state'),identity=str(identity)))==0
    out=capsys.readouterr()
    assert out.out=='https://fixture-abcd.tunnl.gg\nhttps://fixture-abcd.tunnl.gg/mcp/\n'
    assert 'PRIVATE-NOT-FOR-STDOUT' not in out.out+out.err
    assert 'MCP_PUBLIC_HOST' in out.err
    assert not any('secret' in line.lower() for line in out.out.splitlines())


def test_no_server_never_starts_tunnel(tmp_path,monkeypatch):
    monkeypatch.setattr(tunnel.shutil,'which',lambda name:'/usr/bin/'+name)
    monkeypatch.setattr(tunnel.urllib.request,'urlopen',lambda *args,**kwargs: (_ for _ in ()).throw(OSError('not running')))
    monkeypatch.setattr(tunnel.subprocess,'Popen',lambda *args,**kwargs:pytest.fail('must not start SSH'))
    with pytest.raises(ValueError,match='no healthy server'):
        tunnel.run(argparse.Namespace(port=33041,state_dir=str(tmp_path),identity=''))


def test_url_extraction_not_other_hosts():
    assert tunnel.PUBLIC_URL.search('https://evil.test/fake.tunnl.gg') is None
    assert tunnel.PUBLIC_URL.search('https://fixture-abcd.tunnl.gg.evil.test') is None


def test_default_key_create_and_permissions(tmp_path,monkeypatch):
    monkeypatch.setattr(tunnel.shutil,'which',lambda name:'/usr/bin/'+name)
    def generate(command,**kwargs):
        assert command[0]=='ssh-keygen'
        Path(command[-1]).write_text('dummy-key-not-a-secret')
    monkeypatch.setattr(tunnel.subprocess,'run',generate)
    key=tunnel.ensure_key(tmp_path/'private')
    assert key.stat().st_mode&0o777==0o600
    assert key.parent.stat().st_mode&0o777==0o700
    assert tunnel.ensure_key(key.parent)==key
