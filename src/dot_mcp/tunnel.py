"""Foreground tunnl.gg helper. No owner credentials, no server restart."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

PUBLIC_URL = re.compile(r'https://[a-z0-9-]+\.tunnl\.gg(?![a-zA-Z0-9.-])')


def ssh_command(port, identity, known_hosts):
    if not 1 <= int(port) <= 65535:raise ValueError('port must be 1..65535')
    return ['ssh','-t','-i',str(identity),'-o','IdentitiesOnly=yes',
            '-o','StrictHostKeyChecking=accept-new','-o',f'UserKnownHostsFile={known_hosts}',
            '-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',
            '-o','RequestTTY=force','-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=15',
            '-R',f'80:localhost:{int(port)}','stable@proxy.tunnl.gg']


def ensure_key(directory):
    directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
    key=directory/'tunnl-key'
    if key.is_symlink():raise ValueError('refusing symlink SSH identity')
    if not key.exists():
        if not shutil.which('ssh-keygen'):raise ValueError('ssh-keygen unavailable')
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(key)],
                       check=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    key.chmod(0o600)
    return key


def run(args):
    """Only the non-secret URLs go to stdout; provider banners stay private."""
    port=args.port or int(os.environ.get('MCP_PORT','33041'))
    if not 1<=port<=65535:raise ValueError('port must be 1..65535')
    if not shutil.which('ssh'):raise ValueError('ssh unavailable; install OpenSSH')
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=3) as response:
            if not json.load(response).get('ok'):raise ValueError()
    except Exception:
        raise ValueError('no healthy server on loopback port; start server first') from None
    raw_directory=Path(args.state_dir).expanduser()
    if raw_directory.is_symlink():raise ValueError('refusing symlink tunnel state directory')
    directory=raw_directory.resolve()
    directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
    raw_identity=Path(args.identity).expanduser() if args.identity else None
    if raw_identity and raw_identity.is_symlink():raise ValueError('refusing symlink SSH identity')
    identity=raw_identity.resolve() if raw_identity else ensure_key(directory)
    if not identity.is_file():raise ValueError('SSH identity file unavailable')
    command=ssh_command(port,identity,directory/'known_hosts')
    # Keep the provider's full output out of terminal recordings. It can contain
    # banners/control sequences. Do not forward content as executable instructions.
    fd,path=tempfile.mkstemp(prefix='tunnel-',suffix='.log',dir=directory)
    process=None
    try:
        with os.fdopen(fd,'wb') as log:
            process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+45;url=None
            while time.monotonic()<deadline:
                if process.poll() is not None:raise ValueError('tunnel exited before URL; inspect private tunnel log')
                text=Path(path).read_text(errors='replace')
                match=PUBLIC_URL.search(text)
                if match:url=match.group(0);break
                time.sleep(.2)
            if not url:raise ValueError('tunnel URL unavailable after45 seconds; inspect private tunnel log')
            print(url,flush=True);print(url+'/mcp/',flush=True)
            print('Tunnel only; server was not restarted. Configure MCP_PUBLIC_HOST to this host before server boot. Existing server issuer/live origins do not change automatically.',file=sys.stderr)
            print('Private tunnel log: ' + path, file=sys.stderr)
            print('Browser visitors must pass the tunnl.gg warning page. Keep this terminal open. Free-tunnel uptime is not guaranteed.',file=sys.stderr)
            return process.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()


def main(argv=None):
    ap=argparse.ArgumentParser(prog='dots tunnel',description='Start a stable-key tunnl.gg tunnel to an already-running local server')
    ap.add_argument('--port',type=int,default=0)
    ap.add_argument('-i','--identity',default='',help='SSH identity (default: dedicated local tunnl-key)')
    ap.add_argument('--state-dir',default=str(Path.home()/'.dot-mcp/tunnel'),help='private SSH key/known-host/log directory')
    args=ap.parse_args(argv)
    try:return run(args)
    except (ValueError,OSError,subprocess.SubprocessError):
        print('Tunnel failed. Check local server, OpenSSH, identity and private tunnel log. No server settings changed.',file=sys.stderr)
        return 1

if __name__=='__main__':raise SystemExit(main())
