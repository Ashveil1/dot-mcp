import argparse
import json
import pytest
from dot_mcp import cli,orchestrator
from dot_mcp.web_ui import authorize,HEADERS


def test_start_json_never_returns_credentials(tmp_path):
    ctx=orchestrator.StartCtx(port=33041,data_dir=str(tmp_path),secret='OWNER-SECRET-FIXTURE',py='python',env={},repo=tmp_path)
    res=orchestrator.FlowResult(True,public_url='https://example.test/mcp/',host='example.test')
    result=orchestrator.json_result(True,ctx,res,{'client_id':'fixture-client','client_secret':'CLIENT-SECRET-FIXTURE'})
    assert 'OWNER-SECRET-FIXTURE' not in result
    assert 'CLIENT-SECRET-FIXTURE' not in result
    assert json.loads(result)['credentials_hidden']


def test_credentials_redirect_refused(monkeypatch,tmp_path,capsys):
    monkeypatch.setattr(cli,'DEFAULT_DATA',str(tmp_path))
    monkeypatch.setattr('sys.stdout.isatty',lambda:False)
    assert cli.cmd_credentials(argparse.Namespace(data_dir='',show=True))==1
    assert 'requires a private interactive terminal' in capsys.readouterr().err


def test_oauth_escaped_accessible_and_no_external_assets():
    page=authorize('<script>evil</script>','owner:all','<input type="hidden" name="state" value="test">')
    assert '&lt;script&gt;evil&lt;/script&gt;' in page
    assert '<script>evil</script>' not in page
    assert 'for="owner_secret"' in page and 'id="owner_secret"' in page
    assert 'autocomplete="off"' in page
    assert '<script src=' not in page
    assert 'no-store'==HEADERS['Cache-Control']


def test_doctor_json_no_credentials(monkeypatch,capsys):
    monkeypatch.setenv('MCP_PATH_SECRET','NOT-IN-DIAGNOSTICS')
    monkeypatch.setattr('urllib.request.urlopen',lambda *args,**kwargs: (_ for _ in ()).throw(OSError('offline')))
    assert cli.cmd_doctor(argparse.Namespace(port=1,json=True))==0
    out=capsys.readouterr().out
    assert 'NOT-IN-DIAGNOSTICS' not in out
    report=json.loads(out)
    assert not report['running']['ok']
    assert 'mcp' in report['packages']


def test_consent_csp_allows_only_validated_callback_origin():
    from dot_mcp.web_ui import consent_headers
    import pytest
    policy=consent_headers('https://chatgpt.com/connector/oauth/fixture')['Content-Security-Policy']
    assert "form-action 'self' https://chatgpt.com;" in policy
    assert '*' not in policy
    for target in ('http://evil.test/cb','https://user:pass@evil.test/cb','javascript:alert(1)'):
        with pytest.raises(ValueError):consent_headers(target)


def test_start_vault_foreground_no_secret_handoff(tmp_path,monkeypatch):
    monkeypatch.setattr('sys.stdin.isatty',lambda:True)
    monkeypatch.setattr(cli.ui,'_ensure_tunnl_key',lambda *a:tmp_path/'key')
    monkeypatch.setattr(cli.ui,'_start_tunnl',lambda *a:'https://fixture.tunnl.gg')
    monkeypatch.setattr(cli.ui,'_kill_tunnel',lambda *a:None)
    monkeypatch.setenv('MCP_PUBLIC_HOST','fixture.tunnl.gg')
    monkeypatch.setenv('MCP_PORT','33041')
    monkeypatch.setenv('MCP_HOST','127.0.0.1')
    calls=[]
    monkeypatch.setattr('dot_mcp.vault.main',lambda argv:calls.append(argv) or 0)
    monkeypatch.setattr(cli,'get_secret',lambda: (_ for _ in ()).throw(AssertionError('vault main owns local unlock/auth provisioning')))
    result=cli._cmd_start(argparse.Namespace(port=33042,data_dir=str(tmp_path),json=False,vault=True))
    assert result==0
    assert calls==[['serve','--data-dir',str(tmp_path.resolve())]]
    assert __import__('os').environ['MCP_PORT']=='33042'


@pytest.mark.parametrize('tty,json_mode',[(False,True),(True,True),(False,False)])
def test_start_vault_noninteractive_remains_locked(tmp_path,monkeypatch,capsys,tty,json_mode):
    monkeypatch.setattr('sys.stdin.isatty',lambda:tty)
    monkeypatch.setattr('dot_mcp.vault.main',lambda *args: (_ for _ in ()).throw(AssertionError('must not unlock')))
    monkeypatch.setattr(cli,'get_secret',lambda:'fixture-owner-not-output')
    monkeypatch.setattr(cli,'server_python',lambda:('python',None))
    monkeypatch.setattr(cli.subprocess,'run',lambda *args,**kwargs:type('Result',(),{'stdout':'mcp-ok'})())
    monkeypatch.setattr(cli.ui,'plain_flow',lambda *args,**kwargs:orchestrator.FlowResult(True,public_url='https://fixture.test/mcp/',host='fixture.test'))
    monkeypatch.setattr(cli,'_start_oauth_client',lambda *args:{'client_id':'fixture','client_secret':'fixture-client-not-output'})
    assert cli._cmd_start(argparse.Namespace(port=33043,data_dir=str(tmp_path),json=json_mode,vault=True))==0
    output=capsys.readouterr()
    assert 'No interactive vault prompt' in output.err
    assert 'fixture-owner-not-output' not in output.out+output.err
    assert 'fixture-client-not-output' not in output.out+output.err
