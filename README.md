# dot-mcp

Single-file MCP agent แบบ dots สำหรับ Termux / Linux — shell + files + memory + goals + inbox + approvals + rules + scheduler ในไฟล์เดียว (`dot_mcp.py`)

Endpoint: `http://127.0.0.1:8088/mcp` · Health: `/health`

## Features

- **Shell:** `run_command` (background session + timeout) + `read_output`
- **Files:** `list_dir`, `read_file` (paginated + sha256), `write_file` (atomic + sha guard), `append_file`, `edit_file` (unique-match)
- **Memory:** `remember`, `recall`, `forget` — จำข้ามแชท (`~/.dot-mcp/memory.json`)
- **Goals/Tasks:** `set_goal`, `add_update`, `get_state`, `add_task`, `complete_task`
- **Inbox (ข้ามแชท):** `notify_user`, `poll_inbox`, `ack_inbox`
- **Approvals + Rules:** `request_approval`, `answer_approval`, `add_rule`, `list_rules`, `remove_rule` — บังคับฝั่ง server (`ask`/`block` ด้วย regex)
- **Scheduler:** `add_schedule`, `list_schedules`, `remove_schedule` — งานประจำรันเองตอนไม่มีแชท ผลเข้า inbox
- **Embedded UI:** `open_workspace` — MCP Apps dashboard สไตล์ Codex แสดง tasks, memory, command sessions, schedules และ safety rules
- **Auth:** Bearer (`MCP_AUTH_TOKEN`) + DNS rebinding protection (loopback + `MCP_PUBLIC_HOST`)
- รวม 27 tools เมื่อใช้ MCP SDK v2; SDK v1 ใช้ fallback แบบข้อความ

## Quickstart

```bash
./setup.sh   # สร้าง .venv + ติดตั้ง mcp, uvicorn
./run.sh     # เช็ค tools + รันที่ :8088
./test.sh    # curl /health + initialize
```

คุยแบบ local-only (ไม่ต้อง tunnel, ไม่ต้องจ่าย sub):

```bash
export OPENAI_API_KEY=sk-...
.venv/bin/python local_agent.py            # chat loop
.venv/bin/python local_agent.py --list     # ดู tools เฉยๆ
```

เทสผ่าน MCP client จริง:

```bash
.venv/bin/python agent_test.py
.venv/bin/python agent_test.py http://127.0.0.1:8088/mcp
```

เปิดให้ ChatGPT web เรียกผ่าน tunnel:

**วิธีแนะนำ: tunnl.gg (URL สุ่มแต่คงเดิมเมื่อใช้ key เดิม)**

```bash
git clone https://github.com/Ashveil1/dot-mcp.git
cd dot-mcp
./setup.sh
./expose.sh
```

ครั้งแรกสคริปต์จะสร้าง SSH key และ MCP bearer token ให้อัตโนมัติ เก็บไว้ใน `~/.config/dot-mcp/` แล้วแสดง URL `/mcp` และ token สำหรับตั้งค่า connector การรันครั้งต่อไปจะใช้ key และ token เดิม จึงไม่ต้องเปลี่ยน URL หรือแก้ connector ตราบใดที่ยังเก็บโฟลเดอร์นี้ไว้

ข้อจำกัดแผนฟรีของ tunnl.gg: URL เป็นชื่อสุ่มที่ผู้ให้บริการกำหนด (ไม่สามารถเลือก prefix `dot-mcp-` เองได้); tunnel หมดอายุหลัง 24 ชั่วโมง หรือหลังไม่มี traffic 2 ชั่วโมง ต้องรัน `./expose.sh` ใหม่เพื่อเชื่อมต่ออีกครั้ง แต่ URL จะคงเดิมเมื่อใช้ SSH key เดิม ต้องมี OpenSSH (`ssh` และ `ssh-keygen`) และเครื่องต้องออนไลน์ขณะใช้ MCP อย่าลบหรือเผยแพร่ private key และไฟล์ `auth_token` ใน `~/.config/dot-mcp/`.

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
export MCP_AUTH_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
./expose.sh
```

สคริปต์จะตรวจสอบ `/health` และส่ง MCP `initialize` ผ่าน public URL ก่อนแสดง URL สำหรับ ChatGPT หากใช้ ngrok ต้องใช้ `NGROK_DOMAIN` เดิมทุกครั้ง และเก็บ authtoken/MCP token เป็นความลับ การมี URL คงที่ไม่ได้หมายความว่าเซิร์ฟเวอร์จะออนไลน์เมื่อเครื่องปิดอยู่

หมายเหตุ: `expose.sh` ไม่ดาวน์โหลด ngrok ให้อัตโนมัติ เพราะต้องใช้ binary ที่เหมาะกับระบบปฏิบัติการ/สถาปัตยกรรมของเครื่อง โดยเฉพาะ Android/Termux อาจต้องใช้วิธีติดตั้งที่รองรับ Termux โดยตรง

เมื่อเริ่มสำเร็จ ให้เอา `MCP URL` ที่สคริปต์แสดงไปใส่ Settings > Apps > Advanced > Developer Mode > Add connector และตั้ง Bearer token ตามค่าที่สคริปต์แสดง

## Embedded UI in ChatGPT

When connected through a ChatGPT MCP connector that supports MCP Apps, call `open_workspace` to render the responsive Dots Workspace panel. It provides Overview, Tasks, Memory, Activity/Schedules and Rules views. This is an embedded app surface; it cannot change ChatGPT's global sidebar, composer, or native Codex interface. Use MCP SDK v2 for the interactive UI path; the older SDK fallback returns text only.

## Env

| ตัวแปร | ค่าเริ่มต้น | หมายถึง |
|---|---|---|
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8088` | bind server |
| `MCP_AUTH_TOKEN` | `""` (off) | Bearer auth, ว่าง = loopback only |
| `MCP_PUBLIC_HOST` | `""` | tunnel host ที่ allow (`expose.sh` ตั้งให้เอง) |
| `DOT_MCP_DATA` | `~/.dot-mcp` | ที่เก็บ memory/state/inbox/rules/schedules |
| `MCP_TRUNC_LIMIT` | `8192` | ตัด stdout ยาวๆ แล้วให้ต่อด้วย `read_output` |
| `HOME` / `TMPDIR` | — | `resolve()` จำกัด path อยู่ใน HOME/TMPDIR//sdcard, `/tmp` ถูก map ไป `$TMPDIR` (Android ไม่มี /tmp) |

ไฟล์ข้อมูล: `memory.json`, `state.json`, `inbox.json`, `rules.json`, `schedules.json`, `approvals.json`

## Files

```
dot_mcp.py      # MCP server + tools
ui/workspace.html # Codex-inspired embedded workspace UI (MCP Apps)
local_agent.py  # ChatGPT model + MCP local (ไม่ต้อง tunnel)
agent_test.py   # client test: list tools + list_dir/get_state/remember/recall
setup.sh / run.sh / test.sh / expose.sh
requirements.txt  # mcp>=1,<3, uvicorn>=0.30
bin/cloudflared   # auto-download (arm64) — ไม่ commit
```

## Security

- ไม่มี token = ใครมี URL ก็สั่ง shell เครื่องได้ — ใช้ local อย่างเดียว หรือเปิด tunnel เฉพาะตอนใช้ + ปิดทันที
- มี token = ส่ง `Authorization: Bearer xxx` ทุก request (ทั้ง `/mcp`)
- งานเสี่ยง: ตั้ง rule `add_rule` แบบ `ask`/`block` ด้วย regex (เช่น `rm -rf|mkfs|dd `) แล้วให้ agent `request_approval` ก่อนรัน

## License

MIT
