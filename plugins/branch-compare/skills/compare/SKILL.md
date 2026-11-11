---
name: compare
description: >
  Compare two branches of a service before merging one into the other, by default staging into
  release (both can be named). Lists the commits the target is missing with what each does, and
  separates the ones already there (cherry-picked), reverts, and hotfixes only on the target that need
  merging back. Then judges what the merge would really change, from a trial merge: Kafka topics,
  groups and message classes; database migrations (Flyway, Liquibase); config keys, required keys and
  environment files compared with each environment's config; dependencies; API; merge conflicts;
  sensitive business code. Always writes the result to a Markdown file (review-reports/*.md) from a
  fixed template. Read-only: never checks
  out, merges or pushes. Use when the user says "so sánh staging với release", "staging còn commit
  nào chưa lên release", "liệt kê commit thiếu trên release", "đánh giá merge staging lên release",
  "merge staging vào release ảnh hưởng gì", "commit nào chỉ có trên release", "compare staging and
  release".
argument-hint: "[nhánh nguồn=staging] [nhánh đích=release] [--service <tên>] [--send-telegram]"
effort: medium
---

# So sánh nhánh trước khi merge (mặc định staging → release)

Trả lời theo thứ tự:

1. **Commit nào còn thiếu trên nhánh đích**, mỗi commit làm gì. Commit đã cherry-pick thì không tính.
2. **Merge vào thì thực sự đổi gì, ảnh hưởng đến đâu**: Kafka, migration, config, dependency, CI/CD,
   API, xung đột, nghiệp vụ nhạy cảm.
3. **Việc phải làm**: trước khi merge, trước khi deploy, sau khi merge.

Template: `references/report-template.md`. Checklist: `references/checklist.md` (đọc ở Bước 4).

```bash
D="${CLAUDE_PLUGIN_ROOT}/skills/compare/scripts"
# rỗng thì: D="$(dirname "$(find ~/.claude -path '*branch-compare*/scripts/branch_compare.py' | head -1)")"
```

`$TMP` = scratchpad của session (hoặc `mktemp -d`). **Không ghi file tạm vào repo.**

## Bước 1 — Hai nhánh

Nguồn mặc định `staging`, đích mặc định `release`; người dùng nêu nhánh khác thì dùng nhánh đó. Chạy
trong repo của service; không phải repo git thì hỏi đường dẫn (một câu).

```bash
git fetch origin <nguồn> <đích>     # chỉ cập nhật origin/<nhánh>, không đụng working tree
```

Không fetch được (mạng, quyền): làm tiếp với ref đang có, ghi ngày commit đầu mỗi nhánh vào báo cáo.

## Bước 2 — Quét (một lệnh)

```bash
python3 "$D/branch_compare.py" --source <nguồn> --target <đích> \
  --out "$TMP/compare.json" --patch-file "$TMP/merge.patch" --format text
```

Tên trần ưu tiên `origin/<tên>`. Phần in ra là bản tóm tắt; chi tiết trong `compare.json`:

- `range`: ref, sha, ngày, merge-base; `merge` (thử merge: `clean` / `conflict` + file / `unknown`);
  `diff_basis` (`merge-result` = đích → kết quả merge thử; `merge-base` khi không thử được);
  `diff_from`/`diff_to`; `counts`; `dialect`; `env_check_keys`; `report_name`.
- `commits.missing`: commit cần đưa lên, kèm `tickets`, `categories`, `flags`, `areas`.
- `commits.maybe_on_target` (cùng tiêu đề, khác nội dung), `already_on_target` (cùng nội dung hoặc
  cherry-pick -x), `reverted` (cặp commit/revert trong đợt), `no_net_effect` (merge không đổi file nào
  của commit).
- `target_only` (hotfix ở đích; `back_merge_effect` = có cần merge ngược), `partial_tickets`,
  `conflicts` (file + commit hai phía), `target_only_migrations`.
- `findings` trên thay đổi thực: luật của spring-pr-review (SQL theo DB, Liquibase, Flyway, config,
  Kafka, Java) cộng `config-required-key`, `config-target-env-file`, `config-source-env-file`,
  `config-staging-value`, `kafka-topic-declared`, `message-field-*`, `message-schema-changed`,
  `dependency-snapshot`, `version-changed`. Cùng `config_changes`, `required_keys`, `migrations`.

Exit 2: đọc dòng lỗi (thường là nhánh chưa fetch). DB nhận sai: `--dialect mariadb|postgresql|...`.
Môi trường đích khác uat/prod: `--target-envs uat,prod`.

## Bước 3 — Đối chiếu cấu hình môi trường

Khi có `config_changes`, `env_check_keys` hoặc cờ Kafka:

```bash
python3 "$D/env_overrides.py" --service <service> --scan "$TMP/compare.json" \
  --keys "<range.env_check_keys>" --envs staging,uat,production --format md
```

Bảng key × môi trường ("ghi đè: `giá trị`" hoặc "—") là bảng mục 2.3. Key bắt buộc mà uat/production
"—" → 🔴. Cần plugin app-config-review (`APP_CONFIG_*`); không có thì ghi "chưa đối chiếu cấu hình môi
trường". Tên service: thư mục trong repo cấu hình (`spring.application.name` hoặc tên repo).

## Bước 4 — Đọc có chọn lọc, chấm theo checklist

1. **Commit thiếu**: subject + body; tiêu đề mơ hồ hoặc có cờ thì `git show --stat <sha>` rồi phần đổi.
2. **Migration** trong `migrations`: đọc đủ file (`git show <diff_to>:<file>`).
3. **File có cờ high/medium**: thay đổi thực `git diff <diff_from> <diff_to> -- <file>`.
4. **Xung đột**: phía nguồn `git diff <merge_base> <source_sha> -- <file>`, phía đích
   `git diff <merge_base> <target_sha> -- <file>`; nói rõ cùng hay khác logic.
5. **`maybe_on_target`**: `git show` cả hai commit; nội dung trùng thì xếp vào "đã có".
6. **`partial_tickets`, `areas`**: phần còn thiếu có phải thứ phần đã lên cần không.

Patch trên ~500 dòng: tập trung 1–4 và nói rõ. Không checkout, stash, reset, merge, push. Không build
hay chạy test trừ khi được yêu cầu; không chạy thì ghi rõ trong báo cáo.

## Bước 5 — Xuất báo cáo ra file Markdown (bắt buộc)

Kết quả luôn là **một file `.md`** viết theo đúng `references/report-template.md`, kể cả khi người dùng
chỉ hỏi nhanh hay không có commit nào thiếu (khi đó báo cáo ghi rõ "`<đích>` đã có đủ commit của
`<nguồn>`"). Không xuất `.txt`, `.html` hay chỉ trả lời trong chat; trong chat chỉ tóm tắt vài dòng kèm
đường dẫn file.

- **Mục 1** liệt kê đủ `missing`, `maybe_on_target`, `target_only`, `partial_tickets`. `already_on_target`,
  `no_net_effect`, `reverted` gộp thành bảng ngắn (trên 10 dòng thì ghi số + vài ví dụ).
- **Mục 2** theo 8 nhóm của checklist; nhóm trống ghi "Không có", không bỏ mục.
- **Mục 3** việc cần làm: trước merge, trước/trong deploy (# / việc / nơi: Infra, DBA, Dev), sau merge
  (merge ngược hotfix).
- **Kết luận**: Có thể merge / Merge sau khi xử lý / Chưa nên merge, kèm các việc phải chốt.

Chấm điểm:

- 🔴 luôn: xung đột merge chưa có người gỡ; `migration-existing-changed`, `lb-include-changed`,
  `flyway-duplicate-version`, `kafka-replay-risk` (hoặc group mới + môi trường đặt earliest);
  `config-required-key` mà môi trường đích không có (bảng Bước 3); bỏ/đổi field message khi consumer ở
  service khác; ticket lên một phần mà phần thiếu là migration hoặc config; secret viết thẳng đã xác nhận.
- DROP/TRUNCATE/RENAME/đổi kiểu cột: 🔴 hoặc 🟡 kèm điều kiện kiểm, sau khi đọc câu lệnh.
- Cờ khác: kiểm rồi mới xếp; không có bằng chứng thì không báo như lỗi. Mỗi kết luận có bằng chứng
  (file, dòng, key, bảng, topic). Không đoán ngoài diff. Không nhận xét về người viết commit.

**Lưu file**: `review-reports/<range.report_name>` trong repo (tên dạng
`compare-<nguồn>-to-<đích>-<repo>-<YYYYMMDD>.md`), hoặc thư mục người dùng chỉ định nhưng vẫn đuôi
`.md`. Đây là file duy nhất skill ghi vào repo; không `git add`, không commit. Báo đường dẫn đầy đủ.

## Bước 6 — Telegram (chỉ khi người dùng yêu cầu)

Không tự gửi. Khi người dùng nói "gửi telegram" hoặc truyền `--send-telegram`: soạn bản văn bản thuần
dưới ~1500 ký tự (nhánh, số commit thiếu, các mục 🔴, việc ngoài repo, kết luận, đường dẫn báo cáo; không
giá trị secret), gửi một lần qua skill `telegram-send:send` (hoặc subagent
`telegram-send:telegram-sender`). "Đã gửi" chỉ khi lệnh thoát 0. Gửi file `.md` chỉ khi được nói rõ.

## Không làm

- Không checkout, merge, cherry-pick, rebase, push; không tạo hay merge PR.
- Không in giá trị secret, token, URL có mật khẩu vào chat, báo cáo hay Telegram.
- Không kết luận từ cờ khi chưa đọc câu lệnh hoặc đoạn code.
- Không bỏ mục nào của template; trống thì ghi "Không có".
