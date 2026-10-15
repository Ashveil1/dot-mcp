"""Small self-contained HTML views. No remote fonts, assets or scripts."""
import html

STYLE = '''
:root{color-scheme:dark;--bg:#101318;--card:#191f27;--line:#303c4a;--ink:#edf1f5;--muted:#adc0d3;--accent:#8ed5bf}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,sans-serif}main{max-width:620px;margin:6vh auto;padding:24px}article{padding:28px;border:1px solid var(--line);border-radius:16px;background:var(--card)}.brand{font-size:14px;font-weight:700;letter-spacing:.12em;color:var(--accent)}h1{font-size:30px;line-height:1.15;margin:14px 0}p{color:var(--muted)}label{display:block;margin:22px 0 8px;font-weight:600}input,button,select{font:inherit;min-height:48px;border-radius:8px}input{width:100%;padding:12px;background:var(--bg);color:var(--ink);border:1px solid var(--line)}select{width:100%;padding:12px;background:var(--bg);color:var(--ink);border:1px solid var(--line)}input:focus,button:focus-visible{outline:3px solid var(--accent);outline-offset:3px}button{margin-top:18px;width:100%;background:var(--accent);color:#10251f;border:0;font-weight:700;cursor:pointer}.facts{padding:14px 16px;border:1px solid var(--line);border-radius:8px;overflow-wrap:anywhere}.facts dt{color:var(--muted);font-size:13px}.facts dd{margin:0 0 10px}.facts dd:last-child{margin:0}code{font:14px ui-monospace,monospace}.small{font-size:13px}.warning{border-left:3px solid var(--accent);padding-left:14px}a{color:var(--accent)}@media(max-width:480px){main{margin:12px auto;padding:16px}article{padding:20px}h1{font-size:26px}}
'''
HEADERS = {'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"}

def authorize(client,scope,hidden):
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Authorize dot-mcp</title><style>{STYLE}</style></head><body><main><article><div class="brand">DOT-MCP / CONNECT</div><h1>Connect your assistant</h1><p>Approve this connection only if you started it in your assistant's settings.</p><dl class="facts"><dt>Client</dt><dd>{html.escape(client)}</dd><dt>Requested access</dt><dd><code>{html.escape(scope)}</code></dd></dl><p class="warning">Read-only can inspect files, memory, tasks and command output. Full access can run shell commands, change files, control the browser and use vault tools. Neither mode isolates private data. Choose deliberately.</p><form method="post">{hidden}<label for="scope">Access to grant</label><select id="scope" name="scope"><option value="dots:read" selected>Read-only</option><option value="dots:full">Full access (shell, writes, browser, vault)</option></select><label for="owner_secret">Owner secret</label><input id="owner_secret" type="password" name="owner_secret" autocomplete="off" required autofocus spellcheck="false" placeholder="Enter directly, never in chat"><button type="submit">Approve connection</button></form><p class="small">Your secret stays out of the redirect URL. To cancel, close this tab without approving.</p></article></main></body></html>'''


def consent_headers(redirect_uri):
    """Chromium applies form-action to the redirect after a form POST.

    Caller must already validate the registered OAuth redirect. Restrict this
    allowance to its HTTPS origin; never allow arbitrary external forms.
    """
    from urllib.parse import urlsplit
    p = urlsplit(redirect_uri)
    if p.scheme != 'https' or not p.hostname or p.username is not None or p.password is not None:
        raise ValueError('invalid consent redirect')
    host = p.hostname.encode('idna').decode('ascii')
    host = '[' + host + ']' if ':' in host else host
    port = p.port
    target = 'https://' + host + (':' + str(port) if port and port != 443 else '')
    headers = dict(HEADERS)
    # no-referrer serializes Origin as "null" on browser form POSTs, which
    # the consent origin gate correctly rejects. Preserve same-origin Origin
    # without sending the authorization URL as a cross-origin Referer.
    headers['Referrer-Policy'] = 'same-origin'
    headers['Content-Security-Policy'] = headers['Content-Security-Policy'].replace("form-action 'self'", "form-action 'self' " + target)
    return headers


def access_page(clients=None, owner='', token=''):
    esc = lambda value: html.escape(str(value), quote=True)
    hidden = f'<input type="hidden" name="owner_secret" value="{esc(owner)}">'
    rows = ''
    for client in clients or []:
        last = client['last_seen']
        if last:
            from datetime import datetime, timezone
            last = datetime.fromtimestamp(last, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        rows += f'''<section class="facts"><strong>{esc(client['name'])}</strong><p><code>{esc(client['client_id'])}</code><br>{esc(client['scope'])}<br>Last request: {esc(last or 'none observed')}<br>{'Revoked' if client['revoked'] else 'Authorized'}</p><form method="post">{hidden}<input type="hidden" name="action" value="revoke"><input type="hidden" name="client_id" value="{esc(client['client_id'])}"><button>Revoke client</button></form></section>'''
    body = f'''<form method="post"><label for="owner_secret">Owner secret</label><input id="owner_secret" name="owner_secret" type="password" autocomplete="off" required><button>Show authorized clients</button></form>'''
    if clients is not None:
        body = rows + f'''<h2>Create a separate credential</h2><form method="post">{hidden}<input type="hidden" name="action" value="create"><label for="name">Client label</label><input id="name" name="name" maxlength="80" required><label for="scope">Access</label><select id="scope" name="scope"><option value="dots:read">Read-only</option><option value="dots:full">Full access (shell, writes, browser, vault)</option></select><button>Create credential (30 days)</button></form>'''
    if token:
        body = f'<p>Copy this credential into your client, not into chat. It expires in 30 days.</p><textarea readonly aria-label="New credential">{esc(token)}</textarea>' + body
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>dot-mcp access</title><style>{STYLE}select,textarea{{width:100%;padding:12px;background:var(--bg);color:var(--ink);font:inherit}}section{{margin:16px 0}}textarea{{height:130px}}</style></head><body><main><article><div class="brand">DOT-MCP / ACCESS</div><h1>Client access</h1><p>Authorized credentials, not a live connection monitor. Names are client-supplied labels, not verified identities. Last request shows observed credential use; an idle client may still be connected. Owner access cannot be revoked here: rotate your owner secret locally.</p>{body}<p class="small">Revocation blocks the next tool call and token refresh. It does not cancel commands already running or remove copied data. This page keeps the owner secret in hidden form fields while open. Close it when finished.</p></article></main></body></html>'''
