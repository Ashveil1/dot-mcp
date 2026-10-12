import asyncio
import json
import time
import pytest
pytest.importorskip('playwright')
pytest.importorskip('cryptography')
from dot_mcp.vault import LocalVault,VaultError
from test_browser import browser,local_page,find
from test_vault import PASSPHRASE,SECRET

def test_fill_only_no_output_pixels_or_frames(browser,local_page,tmp_path,capsys):
    v=LocalVault(tmp_path/'vault');v.unlock(PASSPHRASE,create=True)
    v.set_entry('fixture',local_page,{'password':SECRET},allow_loopback=True)
    browser.call('open',url=local_page)
    snap=browser.call('snapshot');ref=find(snap,'message')
    # Deliberately hostile page mirrors input into visible text and its title.
    async def attack():await browser.page.evaluate("() => {document.querySelector('[name=message]').oninput=e=>{out.textContent=e.target.value;document.title=e.target.value}}")
    asyncio.run_coroutine_threadsafe(attack(),browser.loop).result()
    result=browser.call('vault_fill',vault=v,name='fixture',key='password',ref=ref)
    assert result['privacy_mode'] and result['filled']
    assert SECRET not in json.dumps(result)
    assert browser.last_frame['type']=='privacy'
    count=browser.frames;time.sleep(.2);assert browser.frames==count
    for action in ('snapshot','screenshot'):
        with pytest.raises(ValueError) as err:browser.call(action)
        assert SECRET not in str(err.value)
    assert SECRET not in capsys.readouterr().out
    assert not list(browser.shots.glob('*.png'))
    browser.call('close');assert not browser.sensitive
    v.close()


def test_fill_refuses_wrong_site(browser,local_page,tmp_path):
    v=LocalVault(tmp_path/'vault');v.unlock(PASSPHRASE,create=True)
    v.set_entry('fixture','https://example.com',{'password':SECRET})
    browser.call('open',url=local_page);snap=browser.call('snapshot')
    with pytest.raises(VaultError):browser.call('vault_fill',vault=v,name='fixture',key='password',ref=find(snap,'message'))
    assert not browser.sensitive
    v.close()


def test_fill_multiple_fields_existing_refs(browser,local_page,tmp_path):
    v=LocalVault(tmp_path/'vault');v.unlock(PASSPHRASE,create=True)
    v.set_entry('fixture',local_page,{'username':'dummy-user','password':SECRET},allow_loopback=True)
    browser.call('open',url=local_page);snap=browser.call('snapshot')
    a=browser.call('vault_fill',vault=v,name='fixture',key='username',ref=find(snap,'message'))
    b=browser.call('vault_fill',vault=v,name='fixture',key='password',ref=find(snap,'password (manual entry only)'))
    assert a['filled'] and b['filled']
    assert SECRET not in json.dumps(b)
    v.close()
