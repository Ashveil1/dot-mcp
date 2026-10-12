import argparse
import json
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
