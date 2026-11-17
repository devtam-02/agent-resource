---
name: sync
description: >
  Sync the user's Obsidian vault to its git remote by asking the running Obsidian app to run the Git
  Vault Sync plugin (command git-vault-sync:sync, or OBSIDIAN_SYNC_COMMAND), then wait until the
  changes are committed and pushed; or show the vault's git state (pending changes, ahead/behind,
  merge conflicts). Never runs git add/commit/push in the vault itself. Use when the user says "đồng
  bộ obsidian", "sync vault", "đẩy ghi chú lên git", "vault đã push chưa", "git vault sync", "obsidian
  có conflict không".
argument-hint: "[status] [--no-wait] [--timeout 90]"
effort: low
---

# Đồng bộ vault Obsidian (Git Vault Sync)

```bash
V="${CLAUDE_PLUGIN_ROOT}/scripts/vault.py"   # không có biến: find ~/.claude/plugins -path '*obsidian-vault*/scripts/vault.py' | head -1
python3 "$V" status                 # chỉ xem: thay đổi chưa commit, chưa đẩy/chưa kéo, conflict
python3 "$V" sync [--timeout 90]    # chạy plugin, chờ commit + push
python3 "$V" sync --no-wait         # chạy plugin rồi thôi
python3 "$V" doctor                 # cấu hình, CLI, plugin có bật không
```

`sync` gọi `obsidian command id=git-vault-sync:sync vault=<tên vault>` (Obsidian CLI, app phải đang
mở), rồi chờ đến khi các thay đổi (trừ `excludePaths` của plugin, như `workspace.json`) được commit và
đẩy lên remote. Chờ tối đa `OBSIDIAN_SYNC_TIMEOUT` giây (mặc định 90). Lệnh có thể mất tới vài chục
giây: chạy nền (`run_in_background`) hoặc giao cho subagent `obsidian-vault:vault-syncer` khi còn việc
khác.

| Mã | Nghĩa | Nói với người dùng |
|---|---|---|
| 0 | `synced: ...` đã commit và đẩy; `started: ...` đã chạy, không chờ | kết quả một dòng |
| 3 | chưa cấu hình `OBSIDIAN_VAULT` | thêm vào `~/.dev-cli/.env` |
| 5 | Obsidian chưa mở, hoặc không có CLI | ghi chú đã nằm trong vault; mở Obsidian (plugin bật sync on startup thì tự đồng bộ) hoặc bật CLI ở Settings > General |
| 6 | đã chạy nhưng chưa commit/đẩy kịp | xem thông báo của Git Vault Sync trong Obsidian (lỗi mạng, token hết hạn); không chạy lại liên tục |
| 7 | vault có conflict | liệt kê file; người dùng giải quyết trong Obsidian (Git Vault Sync: review), hoặc nhờ bạn giải quyết |

## Conflict

Chỉ giải quyết khi người dùng yêu cầu. Khi đó: `status` để thấy file, đọc cả hai phía
(`git -C <vault> diff`, `git show :2:<file>` / `:3:<file>`), giữ nội dung của cả hai khi là ghi chú
(nối các phần khác nhau), lấy bản local cho `.obsidian/workspace*.json`. Ghi file đã gộp, rồi
`git add <file>` và `git commit` trong vault, sau đó chạy `sync` để plugin đẩy lên. Không
`push --force`, không `reset --hard`, không xóa ghi chú để thoát conflict.

## Không làm

- Không tự đồng bộ khi người dùng không yêu cầu (trừ `OBSIDIAN_AUTO_SYNC=on`, xem skill `write`).
- Không chạy `git push` trong vault, không đọc hay in token của plugin
  (`.obsidian/plugins/git-vault-sync/data.json`), không thêm token vào remote URL.
- `doctor` báo `data.json` chưa nằm trong `.gitignore` của vault: nói cho người dùng; chỉ thêm dòng
  `.obsidian/plugins/git-vault-sync/data.json` vào `.gitignore` khi họ đồng ý.
