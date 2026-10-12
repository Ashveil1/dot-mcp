"""Explicit local Secret Service opt-in. No plaintext/keyring fallback backend."""
import os
import secrets
from pathlib import Path
from dot_mcp.vault import LocalVault, VaultError

WARNING = ('Auto-unlock stores the vault passphrase in your OS Secret Service keyring. '
           'Any same-user process with access to the unlocked keyring can unlock your vault. '
           'Only enable on your own trusted machine. Idle auto-lock still applies.')


def marker(data_dir):
    return Path(data_dir).resolve() / 'vault-auto-enabled'


def attributes(data_dir):
    return {'application': 'dot-mcp', 'vault': str(Path(data_dir).resolve() / 'vault.enc')}


def collection():
    try:
        import secretstorage
        bus = secretstorage.dbus_init()
        coll = secretstorage.get_default_collection(bus)
        if coll.is_locked():
            raise VaultError('OS Secret Service keyring is locked; unlock it locally first')
        return coll
    except Exception:
        raise VaultError('OS Secret Service unavailable or locked; no fallback storage used') from None


def find(coll, data_dir):
    items = list(coll.search_items(attributes(data_dir)))
    if len(items) > 1:
        raise VaultError('ambiguous OS keyring entries; auto-unlock refused')
    return items[0] if items else None


def enable(data_dir, passphrase):
    if marker(data_dir).is_symlink(): raise VaultError('opt-in symlink refused')
    v = LocalVault(data_dir)
    try: v.unlock(passphrase)
    finally: v.close()
    coll = collection()
    item = find(coll, data_dir)
    if item and item.is_locked(): raise VaultError('keyring entry locked')
    coll.create_item('dot-mcp vault auto-unlock', attributes(data_dir), passphrase.encode(), replace=True)
    flag = marker(data_dir)
    if flag.is_symlink(): raise VaultError('opt-in symlink refused')
    temp = flag.with_name('vault-auto-' + secrets.token_hex(8) + '.tmp')
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as f: f.write('Secret Service opt-in v1\n')
        os.replace(temp, flag)
    finally: temp.unlink(missing_ok=True)


def disable(data_dir):
    # Stop future auto-unlock even if credential deletion is unavailable.
    marker(data_dir).unlink(missing_ok=True)
    try:
        coll = collection()
        item = find(coll, data_dir)
        if item:
            if item.is_locked(): raise VaultError('keyring entry locked')
            item.delete()
    except Exception:
        raise VaultError('Auto-unlock disabled, but stored credential removal failed. Unlock OS keyring and rerun disable-auto. Stop/restart running server.') from None


def unlock(data_dir):
    flag = marker(data_dir)
    if flag.is_symlink() or not flag.is_file(): return None
    v = LocalVault(data_dir)
    password = None
    try:
        coll = collection()
        item = find(coll, data_dir)
        if not item or item.is_locked(): raise VaultError('keyring entry missing or locked')
        password = item.get_secret().decode()
        v.unlock(password)
        return v
    except Exception:
        v.close()
        return None
    finally: password = None
