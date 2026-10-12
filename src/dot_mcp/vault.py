"""Optional passphrase vault. Fill-only API, not a boundary against same-user shell."""
from __future__ import annotations
import base64
import getpass
import json
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit

class VaultError(ValueError):
    pass


def origin(url, allow_loopback=False):
    try:
        p = urlsplit(url)
        host = p.hostname
        port = p.port
        if p.username is not None or p.password is not None or not host:
            raise ValueError()
        if p.scheme != 'https' and not (allow_loopback and p.scheme == 'http' and host in ('localhost','127.0.0.1','::1')):
            raise ValueError()
        host = host.encode('idna').decode().lower()
        host = '['+host+']' if ':' in host else host
        port = port or (443 if p.scheme == 'https' else 80)
        return f'{p.scheme}://{host}:{port}'
    except (ValueError, UnicodeError):
        raise VaultError('vault origin must be exact HTTPS origin (explicit loopback HTTP is development-only)') from None


class LocalVault:
    """One cooperating writer per encrypted file; locked unless human unlocks."""
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'vault.enc'
        self.key = None
        self.salt = None
        self.entries = None
        self.last_used = 0
        self.lock = threading.RLock()
        self.lease = None

    def _crypto(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
            return AESGCM, Scrypt
        except ImportError:
            raise VaultError("install optional vault extra: pip install 'dot-mcp[vault]'") from None

    def _claim(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        # No following symlinks when opening cooperating process lock.
        fd = os.open(str(self.path)+'.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
        self.lease = os.fdopen(fd,'a+b')
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.lease.close();self.lease=None
            raise VaultError('vault in use by another process; lock/stop that process first') from None

    def unlock(self, passphrase, create=False):
        with self.lock:
            if self.key is not None: raise VaultError('vault already unlocked')
            if not isinstance(passphrase,str) or len(passphrase)<12:
                raise VaultError('use a passphrase of at least 12 characters')
            AESGCM, Scrypt = self._crypto()
            self._claim()
            try:
                if self.path.is_symlink(): raise VaultError('vault symlink refused')
                if not self.path.exists():
                    if not create: raise VaultError('vault not initialized; use dot-mcp vault init')
                    self.salt=secrets.token_bytes(16);nonce=None
                else:
                    if self.path.stat().st_size>2_000_000:raise VaultError('invalid vault')
                    doc=json.loads(self.path.read_text())
                    if doc.get('v')!=1:raise VaultError('invalid vault')
                    self.salt=base64.b64decode(doc['salt'],validate=True)
                    nonce=base64.b64decode(doc['nonce'],validate=True)
                    if len(self.salt)!=16 or len(nonce)!=12:raise VaultError('invalid vault')
                self.key=Scrypt(salt=self.salt,length=32,n=2**15,r=8,p=1).derive(passphrase.encode())
                self.entries={} if nonce is None else json.loads(AESGCM(self.key).decrypt(nonce,base64.b64decode(doc['ciphertext'],validate=True),b'dot-mcp-vault-v1'))
                if not isinstance(self.entries,dict):raise VaultError('invalid vault')
                self.last_used=time.monotonic()
                if nonce is None:self._save()
            except Exception:
                self.close()
                raise VaultError('vault unlock failed (wrong passphrase, invalid file or unavailable storage)') from None

    def _ready(self):
        if self.key is None or self.entries is None:raise VaultError('vault locked; human must run dot-mcp vault serve')
        if time.monotonic()-self.last_used>900:
            self.close();raise VaultError('vault auto-locked after 15 minutes idle; human unlock required')
        self.last_used=time.monotonic()

    def _save(self):
        AESGCM,_=self._crypto()
        nonce=secrets.token_bytes(12)
        ciphertext=AESGCM(self.key).encrypt(nonce,json.dumps(self.entries).encode(),b'dot-mcp-vault-v1')
        doc={'v':1,'salt':base64.b64encode(self.salt).decode(),'nonce':base64.b64encode(nonce).decode(),'ciphertext':base64.b64encode(ciphertext).decode()}
        tmp=self.path.with_name('vault-'+secrets.token_hex(8)+'.tmp')
        try:
            fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as f:json.dump(doc,f);f.flush();os.fsync(f.fileno())
            os.replace(tmp,self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def set_entry(self,name,url,fields,allow_loopback=False):
        with self.lock:
            self._ready()
            if not isinstance(name,str) or not 1<=len(name)<=100 or any(ord(c)<32 for c in name):raise VaultError('invalid entry name')
            if not isinstance(fields,dict) or not fields or not set(fields)<= {'username','password','token'}:raise VaultError('invalid entry fields')
            if any(not isinstance(v,str) or not v or len(v)>4096 for v in fields.values()):raise VaultError('invalid field values')
            self.entries[name]={'origin':origin(url,allow_loopback),'fields':dict(fields),'allow_loopback':bool(allow_loopback)}
            self._save()

    def names(self):
        with self.lock:self._ready();return sorted(self.entries)

    def _for_fill(self,name,key,url):
        # Private to browser fill. Never register this method as a tool.
        with self.lock:
            self._ready();entry=self.entries.get(name)
            if not entry or key not in entry['fields']:raise VaultError('entry or field unavailable')
            if origin(url,entry.get('allow_loopback',False))!=entry['origin']:raise VaultError('vault origin mismatch; fill refused')
            return entry['fields'][key]

    def delete(self,name):
        with self.lock:self._ready();self.entries.pop(name,None);self._save()

    def close(self):
        with self.lock:
            self.key=self.entries=self.salt=None
            if self.lease:self.lease.close();self.lease=None

ACTIVE_VAULT = None

def main(argv=None):
    import argparse
    ap=argparse.ArgumentParser(prog='dot-mcp vault')
    ap.add_argument('action',choices=['init','set','list','delete','serve'])
    ap.add_argument('--data-dir',default=os.environ.get('DOT_MCP_DATA',str(Path.home()/'.dot-mcp')))
    ap.add_argument('--name',default='')
    ap.add_argument('--origin',default='')
    ap.add_argument('--field',action='append',choices=['username','password','token'])
    ap.add_argument('--allow-loopback-http',action='store_true')
    args=ap.parse_args(argv)
    v=LocalVault(args.data_dir)
    try:
        if not __import__("sys").stdin.isatty(): raise VaultError("vault requires a private interactive terminal")
        password=getpass.getpass('Vault passphrase (not echoed): ')
        if args.action=='init' and password!=getpass.getpass('Repeat passphrase: '):raise VaultError('passphrases differ')
        v.unlock(password,create=args.action=='init');password=None
        if args.action=='set':
            if not args.name or not args.origin or not args.field:raise VaultError('set requires --name --origin --field')
            values={key:getpass.getpass(f'{key} (not echoed): ') for key in args.field}
            v.set_entry(args.name,args.origin,values,args.allow_loopback_http);values=None
        elif args.action=='list':print(json.dumps({'names':v.names()}))
        elif args.action=='delete':v.delete(args.name)
        elif args.action=='serve':
            global ACTIVE_VAULT
            ACTIVE_VAULT=v
            # python -m dot_mcp.vault executes this file as __main__; server imports
            # the canonical module name. Share the explicitly unlocked instance.
            import importlib
            importlib.import_module('dot_mcp.vault').ACTIVE_VAULT=v
            os.environ['DOT_MCP_DATA']=str(Path(args.data_dir).resolve())
            if not os.environ.get('MCP_PATH_SECRET'):
                from dot_mcp.cli import get_secret
                os.environ['MCP_PATH_SECRET']=get_secret()
            # Playwright protocol debugging can include typed values. Avoid
            # accidental process-level debug logging on the unlocked path.
            os.environ.pop('DEBUG', None)
            os.environ.pop('PWDEBUG', None)
            import runpy
            runpy.run_module('dot_mcp.server',run_name='__main__')
        else:print('Vault initialized.')
        return 0
    except (VaultError,KeyboardInterrupt):
        print('Vault operation failed or cancelled; no secret values were printed.',file=__import__('sys').stderr)
        return 1
    finally:v.close()

if __name__=='__main__':raise SystemExit(main())
