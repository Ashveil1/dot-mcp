# dot-mcp

Single-file MCP agent แบบ dots สำหรับ Termux / Linux — shell + files + memory + goals + inbox + scheduler ในไฟล์เดียว (`dot_mcp.py`)

> ⚠️ URL สะอาด (`/mcp/`) — auth เป็น Bearer 2 แบบ: JWT จาก OAuth (ให้ ChatGPT/Claude/Gemini) หรือ secret เดิมใน header (ให้สคริปต์ local) ไม่มี header = 401 แม้ localhost

Endpoint: `http://127.0.0.1:33041/mcp/` · Health: `/health`

## Features

- **Shell:** `run_command` (background session + timeout) + `read_output`
- **Files:** `list_dir`, `read_file` (paginated + sha256), `write_file` (atomic + sha guard), `append_file`, `edit_file` (unique-match)
- **Memory:** `remember`, `recall`, `forget` — จำข้ามแชท (`~/.dot-mcp/memory.json`)
- **Goals/Tasks:** `set_goal`, `add_update`, `get_state`, `add_task`, `complete_task`
- **Inbox (ข้ามแชท):** `notify_user`, `poll_inbox`, `ack_inbox`
- **Scheduler:** `add_schedule`, `list_schedules`, `remove_schedule` — งานประจำรันเองตอนไม่มีแชท ผลเข้า inbox
- **Embedded UI:** `open_workspace` — MCP Apps dashboard สไตล์ Codex แสดง tasks, memory, command sessions และ schedules
- รวม 22 tools

## Quickstart

**ติดตั้งจาก PyPI:**

```bash
pip install dot-mcp
dot-mcp start      # server + tunnel + public URL (port 33041, secret ประจำเครื่อง)
dot-mcp status     # เช็คว่ารันอยู่ไหม
dot-mcp stop       # หยุด server + tunnel
```

`dot-mcp start` รัน server ในพื้นหลังแล้วคืน shell ทันที — จะได้ URL,
owner secret, OAuth client ID/secret ให้เอาไปตั้ง connector เลย

**รันจาก source checkout:**

```bash
./setup.sh   # สร้าง .venv + ติดตั้ง deps + editable install
./dots start # เหมือนด้านบน
```

สำรอง: `./run.sh` (server เฉยๆ ไม่มี tunnel) · `./test.sh` (health + initialize) ·
`./expose.sh` (tunnel เฉยๆสำหรับ ChatGPT connector)

คุยแบบ local-only (ไม่ต้อง tunnel, ไม่ต้องจ่าย sub):

```bash
export OPENAI_API_KEY=sk-...
python -m dot_mcp.local_agent          # chat loop
python -m dot_mcp.local_agent --list   # ดู tools เฉยๆ
```

เทสผ่าน MCP client จริง:

```bash
.venv/bin/python agent_test.py
.venv/bin/python agent_test.py http://127.0.0.1:33041/mcp/   # อ่าน secret จาก env/ไฟล์ให้เอง
```

เปิดให้ ChatGPT web เรียกผ่าน tunnel:

**วิธีแนะนำ: tunnl.gg (URL สุ่มแต่คงเดิมเมื่อใช้ key เดิม)**

```bash
git clone https://github.com/Ashveil1/dot-mcp.git
cd dot-mcp
./setup.sh
./expose.sh
```

ครั้งแรกสคริปต์จะสร้าง SSH key และ owner secret ให้อัตโนมัติ เก็บไว้ใน `~/.config/dot-mcp/` (`owner_secret` 0600) แล้วแสดง URL `/mcp/` เส้นสะอาดสำหรับตั้งค่า connector (auth = OAuth) การรันครั้งต่อไปใช้ key และ secret เดิม URL และ consent ไม่เปลี่ยน ตราบใดที่ยังเก็บโฟลเดอร์นี้ไว้

ข้อจำกัดแผนฟรีของ tunnl.gg: URL เป็นชื่อสุ่มที่ผู้ให้บริการกำหนด (ไม่สามารถเลือก prefix `dot-mcp-` เองได้); tunnel หมดอายุหลัง 24 ชั่วโมง หรือหลังไม่มี traffic 2 ชั่วโมง ต้องรัน `./expose.sh` ใหม่เพื่อเชื่อมต่ออีกครั้ง แต่ URL จะคงเดิมเมื่อใช้ SSH key เดิม ต้องมี OpenSSH (`ssh` และ `ssh-keygen`) และเครื่องต้องออนไลน์ขณะใช้ MCP อย่าเผยแพร่ private key ใน `~/.config/dot-mcp/`

สคริปต์จะตรวจ MCP `initialize` ผ่าน public HTTPS ก่อนแสดง URL หากการทดสอบไม่ผ่านจะรายงานข้อผิดพลาดแทนการบอกว่าเชื่อมต่อสำเร็จ MCP สคริปต์จะรายงานว่าไม่ผ่านแทนการแสดงว่าใช้งานได้ URL แบบสุ่มนี้ **ไม่คงเดิมเมื่อรันใหม่** และชื่อที่ขึ้นต้น `dot-mcp-` ไม่ได้แปลว่าถูกจองถาวร

**Cloudflare Quick Tunnel (URL สุ่ม):**

```bash
TUNNEL_PROVIDER=cloudflare ./expose.sh
```

**URL คงที่ด้วย ngrok (ต้องตั้งค่าบัญชีครั้งเดียว):**

1. สร้างบัญชี ngrok และคัดลอก authtoken จาก dashboard
2. คัดลอกโดเมนฟรีที่ ngrok assign ให้บัญชี (เช่น `example.ngrok-free.app`) จาก dashboard
3. ติดตั้ง ngrok binary ให้ตรงกับ OS/architecture ของเครื่อง แล้วตั้งค่าตัวแปรและเริ่ม:

```bash
export TUNNEL_PROVIDER=ngrok
export NGROK_AUTHTOKEN='ใส่-authtoken-ของคุณ'
export NGROK_DOMAIN='โดเมนที่-ngrok-assign-ให้คุณ'
./expose.sh
```

สคริปต์จะตรวจสอบ `/health` และส่ง MCP `initialize` ผ่าน public URL ก่อนแสดง URL สำหรับ ChatGPT หากใช้ ngrok ต้องใช้ `NGROK_DOMAIN` เดิมทุกครั้ง และเก็บ authtoken เป็นความลับ การมี URL คงที่ไม่ได้หมายความว่าเซิร์ฟเวอร์จะออนไลน์เมื่อเครื่องปิดอยู่

หมายเหตุ: `expose.sh` ไม่ดาวน์โหลด ngrok ให้อัตโนมัติ เพราะต้องใช้ binary ที่เหมาะกับระบบปฏิบัติการ/สถาปัตยกรรมของเครื่อง โดยเฉพาะ Android/Termux อาจต้องใช้วิธีติดตั้งที่รองรับ Termux โดยตรง

เมื่อเริ่มสำเร็จ ให้เอา `MCP URL` ที่สคริปต์แสดงไปใส่ Settings > Apps > Advanced > Developer Mode > Add connector (ไม่ต้องตั้ง token)

## Embedded UI in ChatGPT

When connected through a ChatGPT MCP connector that supports MCP Apps, call `open_workspace` to render the responsive Dots Workspace panel. It provides Overview, Tasks, Memory, Activity/Schedules and Rules views. This is an embedded app surface; it cannot change ChatGPT's global sidebar, composer, or native Codex interface. Use MCP SDK v2 for the interactive UI path; the older SDK fallback returns text only.

## Env

| ตัวแปร | ค่าเริ่มต้น | หมายถึง |
|---|---|---|
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `33041` | bind server |
| `MCP_PUBLIC_HOST` | `""` | tunnel host ที่ allow (`expose.sh` ตั้งให้เอง) |
| `DOT_MCP_DATA` | `~/.dot-mcp` | ที่เก็บ memory/state/inbox/schedules |
| `MCP_TRUNC_LIMIT` | `8192` | ตัด stdout ยาวๆ แล้วให้ต่อด้วย `read_output` |
| `HOME` / `TMPDIR` | — | `resolve()` จำกัด path อยู่ใน HOME/TMPDIR//sdcard, `/tmp` ถูก map ไป `$TMPDIR` (Android ไม่มี /tmp) |

ไฟล์ข้อมูล: `memory.json`, `state.json`, `inbox.json`, `schedules.json`

## Files

```
dots            # CLI: start / status / stop (port 33041 default)
dot_mcp.py      # MCP server + tools
ui/workspace.html # Codex-inspired embedded workspace UI (MCP Apps)
local_agent.py  # ChatGPT model + MCP local (ไม่ต้อง tunnel)
agent_test.py   # client test: list tools + list_dir/get_state/remember/recall
setup.sh / run.sh / test.sh / expose.sh
requirements.txt  # mcp>=1,<3, uvicorn>=0.30
bin/cloudflared   # auto-download (arm64) — ไม่ commit
```

## Security

- `/mcp/` ต้องมี `Authorization: Bearer` เสมอ (JWT จาก OAuth หรือ owner secret) — ไม่มี = 401 แม้ localhost (ตั้งใจ: traffic จาก tunnel ดูเหมือน loopback แยกไม่ออก)
- Owner secret อยู่ใน header/consent form ไม่โผล่ใน URL, log (`mcp.log` ตั้ง `600` + ปิด access log), เปิด tunnel เฉพาะตอนใช้ + ปิดทันที

## OAuth (ChatGPT / Claude / Gemini)

ฟรี ทำเองในไฟล์เดียว ไม่ต้องมี IdP เจ้าอื่น — server นี้เป็นทั้งคนออกและคนตรวจ token:

- Metadata: `/.well-known/oauth-authorization-server` + `/.well-known/oauth-protected-resource`
- `POST /register` — Dynamic Client Registration (รับ `redirect_uris` https, หรือ `http://localhost`/`127.0.0.1`)
- `GET/POST /authorize` — หน้า consent ครั้งเดียว ปลดล็อกด้วย owner secret (code + PKCE S256, อายุ 10 นาที, ใช้ครั้งเดียว)
- `POST /token` — แลก code เป็น JWT (1 ชม.) + refresh token (30 วัน, หมุนทุกครั้งที่ใช้)
- เรียก MCP ใส่ `Authorization: Bearer <jwt>` ได้เลย (OAuth) หรือ `Bearer <owner secret>` (สคริปต์ local)
- ตั้งค่า connector: URL = `https://<host>/mcp/`, auth = OAuth, issuer = `https://<host>` (ดูจาก `/.well-known/...` ได้)
- connector แบบ DCR (ChatGPT) สมัคร client เอง + PKCE; แบบกรอกมือ (Claude) ใช้ **OAuth client ID/secret** ประจำเครื่องที่ `dots start` โชว์ (เก็บใน `oauth.json` 0600, ไม่ต้อง register)

## License

MIT
