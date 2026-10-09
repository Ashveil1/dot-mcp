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
- **Auth:** Bearer (`MCP_AUTH_TOKEN`) + DNS rebinding protection (loopback + `MCP_PUBLIC_HOST`)
- รวม 26 tools

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

เปิดให้ ChatGPT web เรียก (tunnel ชั่วคราว):

```bash
./expose.sh                       # ไม่มี auth (สะดวก แต่ใครมี URL ใช้ได้)
MCP_AUTH_TOKEN=xxx ./expose.sh    # มี Bearer auth (แนะนำ)
# -> เอา URL/mcp ไปใส่ Settings > Apps > Advanced > Developer Mode > Add connector
# เลิกใช้: pkill -f '[c]loudflared tunnel'
```

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
dot_mcp.py      # server ทั้งหมด (26 tools)
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
