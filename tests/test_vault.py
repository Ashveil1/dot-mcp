import asyncio
import base64
import json
from pathlib import Path
import time
import pytest
pytest.importorskip('cryptography')
from dot_mcp.vault import LocalVault,VaultError,origin

PASSPHRASE='test long passphrase only'
SECRET='TEST-VAULT-UNIQUE-secret-9482'

def test_encryption_lock_wrong_passphrase_origin(tmp_path):
    v=LocalVault(tmp_path);v.unlock(PASSPHRASE,create=True)
    v.set_entry('fixture','https://example.com',{'password':SECRET})
    assert v.names()==['fixture']
    assert SECRET not in v.path.read_text()
    assert 'fixture' not in v.path.read_text()
    assert v.path.stat().st_mode & 0o777 == 0o600
    assert v._for_fill('fixture','password','https://example.com/login')==SECRET
    for url in ('https://example.com.evil.test','http://example.com','https://example.com:444','https://evil.test','https://example.com@evil.test'):
        with pytest.raises(VaultError):v._for_fill('fixture','password',url)
    v.close()
    with pytest.raises(VaultError):v.names()
    with pytest.raises(VaultError):v.unlock('wrong but long enough')
    v.unlock(PASSPHRASE);assert v.names()==['fixture'];v.close()


def test_integrity_expiry_concurrent_owner(tmp_path):
    v=LocalVault(tmp_path);v.unlock(PASSPHRASE,create=True)
    other=LocalVault(tmp_path)
    with pytest.raises(VaultError):other.unlock(PASSPHRASE)
    v.last_used=time.monotonic()-901
    with pytest.raises(VaultError):v.names()
    v.unlock(PASSPHRASE);v.close()
    doc=json.loads(v.path.read_text())
    ciphertext=bytearray(base64.b64decode(doc['ciphertext'],validate=True))
    ciphertext[0]^=1  # guaranteed changed decoded byte, not a possibly identical base64 character
    doc['ciphertext']=base64.b64encode(ciphertext).decode()
    v.path.write_text(json.dumps(doc))
    with pytest.raises(VaultError):v.unlock(PASSPHRASE)


def test_explicit_local_only_http(tmp_path):
    assert origin('https://EXAMPLE.com/a')=='https://example.com:443'
    with pytest.raises(VaultError):origin('http://127.0.0.1:8000')
    assert origin('http://127.0.0.1:8000',True)=='http://127.0.0.1:8000'
    with pytest.raises(VaultError):origin('http://192.168.1.1',True)

def test_cli_serve_shares_unlocked_instance(tmp_path,monkeypatch):
    import dot_mcp.vault as module
    v=LocalVault(tmp_path);v.unlock(PASSPHRASE,create=True);v.close()
    monkeypatch.setattr('sys.stdin.isatty',lambda:True)
    monkeypatch.setattr(module.getpass,'getpass',lambda *args:PASSPHRASE)
    def fake_run(name,run_name):
        assert name=='dot_mcp.server'
        assert module.ACTIVE_VAULT.names()==[]
    monkeypatch.setattr('runpy.run_module',fake_run)
    assert module.main(['serve','--data-dir',str(tmp_path)])==0
    with pytest.raises(VaultError):module.ACTIVE_VAULT.names()


def test_symlink_refused(tmp_path):
    outside=tmp_path/'outside';outside.write_text('do not change')
    folder=tmp_path/'vault';folder.mkdir();(folder/'vault.enc').symlink_to(outside)
    with pytest.raises(VaultError):LocalVault(folder).unlock(PASSPHRASE,create=True)
    assert outside.read_text()=='do not change'


def test_cli_refuses_redirected_secret_input(tmp_path,monkeypatch,capsys):
    import dot_mcp.vault as module
    monkeypatch.setattr('sys.stdin.isatty',lambda:False)
    monkeypatch.setattr(module.getpass,'getpass',lambda *args:pytest.fail('must not read secrets without TTY'))
    assert module.main(['init','--data-dir',str(tmp_path)])==1
    assert not (tmp_path/'vault.enc').exists()
    assert SECRET not in capsys.readouterr().err


def test_cli_serve_reuses_owner_credential(tmp_path,monkeypatch):
    import dot_mcp.vault as module
    v=LocalVault(tmp_path);v.unlock(PASSPHRASE,create=True);v.close()
    monkeypatch.setattr('sys.stdin.isatty',lambda:True)
    monkeypatch.setattr(module.getpass,'getpass',lambda *args:PASSPHRASE)
    monkeypatch.setattr('dot_mcp.cli.get_secret',lambda:'owner-fixture-secret')
    monkeypatch.delenv('MCP_PATH_SECRET',raising=False)
    monkeypatch.setattr('runpy.run_module',lambda *args,**kwargs:None)
    assert module.main(['serve','--data-dir',str(tmp_path)])==0
    assert __import__('os').environ['MCP_PATH_SECRET']=='owner-fixture-secret'
    # Environment mutation is intentionally for launched server. Clean this test.
    monkeypatch.delenv('MCP_PATH_SECRET',raising=False)


def test_start_vault_actual_local_unlock_without_child(tmp_path,monkeypatch):
    import argparse
    import os
    from dot_mcp import cli
    import dot_mcp.vault as module
    v=LocalVault(tmp_path);v.unlock(PASSPHRASE,create=True);v.close()
    for key in ('MCP_PORT','MCP_HOST','DOT_MCP_DATA','MCP_PATH_SECRET'):
        monkeypatch.setenv(key,os.environ.get(key,''))
    monkeypatch.delenv('MCP_PATH_SECRET',raising=False)
    monkeypatch.setenv('MCP_PUBLIC_HOST','fixture.tunnl.gg')
    monkeypatch.setattr('sys.stdin.isatty',lambda:True)
    monkeypatch.setattr(cli.ui,'_ensure_tunnl_key',lambda *a:tmp_path/'key')
    monkeypatch.setattr(cli.ui,'_start_tunnl',lambda *a:'https://fixture.tunnl.gg')
    monkeypatch.setattr(cli.ui,'_kill_tunnel',lambda *a:None)
    monkeypatch.setenv('MCP_PUBLIC_HOST','fixture.tunnl.gg')
    prompts=[]
    def local_prompt(prompt):
        prompts.append(prompt)
        return PASSPHRASE
    monkeypatch.setattr(module.getpass,'getpass',local_prompt)
    monkeypatch.setattr(cli,'get_secret',lambda:'owner-fixture-only')
    monkeypatch.setattr(cli.ui,'plain_flow',lambda *a,**kw:pytest.fail('must not detach or tunnel'))
    monkeypatch.setattr(cli.subprocess,'Popen',lambda *a,**kw:pytest.fail('must not hand off key to child'))
    def foreground(name,run_name):
        assert name=='dot_mcp.server' and run_name=='__main__'
        assert module.ACTIVE_VAULT.names()==[]
        assert os.environ['DOT_MCP_DATA']==str(tmp_path.resolve())
        assert os.environ['MCP_PORT']=='33044'
        assert os.environ['MCP_HOST']=='127.0.0.1'
        assert os.environ['MCP_PUBLIC_HOST']=='fixture.tunnl.gg'
        assert os.environ['MCP_PATH_SECRET']=='owner-fixture-only'
        assert PASSPHRASE not in os.environ.values()
    monkeypatch.setattr('runpy.run_module',foreground)
    assert cli._cmd_start(argparse.Namespace(port=33044,data_dir=str(tmp_path),json=False,vault=True))==0
    assert len(prompts)==1
    with pytest.raises(VaultError):module.ACTIVE_VAULT.names()
