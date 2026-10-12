import pytest
pytest.importorskip('cryptography')
from dot_mcp import vault_auto as auto
from dot_mcp.vault import LocalVault,VaultError

PASSWORD='fixture auto-unlock passphrase'

@pytest.fixture
def setup(tmp_path,monkeypatch):
    v=LocalVault(tmp_path);v.unlock(PASSWORD,create=True);v.close()
    class Item:
        locked=False
        value=PASSWORD.encode()
        def is_locked(self):return self.locked
        def get_secret(self):return self.value
        def delete(self):coll.items=[]
    class Collection:
        items=[]
        def search_items(self,attrs):
            assert attrs==auto.attributes(tmp_path)
            return self.items
        def create_item(self,label,attrs,value,replace):
            item=Item();item.value=value;self.items=[item]
    coll=Collection()
    monkeypatch.setattr(auto,'collection',lambda:coll)
    return tmp_path,coll


def test_enable_unlock_disable_secret_only_in_keyring(setup):
    path,coll=setup
    assert auto.unlock(path) is None
    auto.enable(path,PASSWORD)
    assert auto.marker(path).stat().st_mode&0o777==0o600
    assert PASSWORD not in auto.marker(path).read_text()
    v=auto.unlock(path);assert v.names()==[];v.close()
    auto.enable(path,PASSWORD) # repeat local opt-in replaces, no duplicates
    auto.disable(path)
    assert coll.items==[] and not auto.marker(path).exists()
    assert auto.unlock(path) is None


@pytest.mark.parametrize('kind',['locked','wrong','missing','duplicate','unavailable'])
def test_fail_closed(setup,monkeypatch,kind):
    path,coll=setup;auto.enable(path,PASSWORD)
    if kind=='locked':coll.items[0].locked=True
    elif kind=='wrong':coll.items[0].value=b'wrong passphrase fixture'
    elif kind=='missing':coll.items=[]
    elif kind=='duplicate':coll.items*=2
    else:monkeypatch.setattr(auto,'collection',lambda:(_ for _ in ()).throw(VaultError('offline')))
    assert auto.unlock(path) is None


def test_no_opt_in_never_contacts_keyring(tmp_path,monkeypatch):
    monkeypatch.setattr(auto,'collection',lambda:pytest.fail('no consent marker'))
    assert auto.unlock(tmp_path) is None


def test_disable_failure_removes_opt_in(setup,monkeypatch):
    path,coll=setup;auto.enable(path,PASSWORD)
    monkeypatch.setattr(auto,'collection',lambda:(_ for _ in ()).throw(VaultError('offline')))
    with pytest.raises(VaultError,match='disabled, but stored credential removal failed'):auto.disable(path)
    assert not auto.marker(path).exists()
    assert auto.unlock(path) is None


def test_wrong_enable_does_not_store_or_opt_in(setup):
    path,coll=setup
    with pytest.raises(VaultError):auto.enable(path,'incorrect fixture passphrase')
    assert coll.items==[] and not auto.marker(path).exists()


def test_backend_missing_fails_without_plaintext_fallback(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules,'secretstorage',None)
    with pytest.raises(VaultError,match='no fallback'):auto.collection()


def test_enable_requires_explicit_local_confirmation(setup,monkeypatch,capsys):
    from dot_mcp import vault
    path,coll=setup
    monkeypatch.setattr('sys.stdin.isatty',lambda:True)
    monkeypatch.setattr('builtins.input',lambda *a:'no')
    monkeypatch.setattr(vault.getpass,'getpass',lambda *a:pytest.fail('no confirmation'))
    assert vault.main(['enable-auto','--data-dir',str(path)])==1
    assert coll.items==[]
    assert 'Any same-user process' in capsys.readouterr().out


def test_noninteractive_opt_in_refused(setup,monkeypatch):
    from dot_mcp import vault
    path,coll=setup
    monkeypatch.setattr('sys.stdin.isatty',lambda:False)
    monkeypatch.setattr('builtins.input',lambda *a:pytest.fail('no TTY'))
    assert vault.main(['enable-auto','--data-dir',str(path)])==1
    assert coll.items==[]
