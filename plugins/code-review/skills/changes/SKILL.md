---
name: changes
description: >
  List what changed between two revisions (tags, branches or commits) of one or more Spring services:
  first the business flows BA/Test must accept (business words, no class names), then for developers a
  copy-ready config block, commits grouped by ticket, API (endpoints served and Feign calls, request/
  response fields, breaking changes), Kafka, database (schema per table, migrations, data changes and
  seeds, entity mismatches), dependencies, and links between services (a changed endpoint another
  service calls, a shared topic). Writes one note per service into the Obsidian vault set in
  ~/.dev-cli/.env (<dir>/<repo>/<yyyymmdd-HHMM>-<from>~<to>.md), then runs the vault's sync. One subagent per service (Sonnet), publishing on Haiku. Read-only on the repositories. Use when
  the user says "liệt kê thay đổi giữa 2 tag", "từ v1.4 đến v1.5 thay đổi gì", "release này thay đổi gì",
  "so sánh tag", "thay đổi nghiệp vụ, cấu hình, database giữa", "changelog kỹ thuật", "thay đổi giữa
  staging và release của các service", "what changed between". For merge risk before merging one branch
  into another, use code-review:compare instead.
argument-hint: "[from=auto] [to=HEAD] [--services a,b | --all <thư mục>]"
effort: medium
---

# Thay đổi giữa hai mốc (tag, nhánh, commit)

Hai nhóm người đọc: BA/Test biết nghiệm thu nghiệp vụ nào (mục 1), dev nắm thay đổi cấu hình, API, Kafka,
database, schema, seed (mục 2–9, ngắn, dạng bảng). Mỗi service một báo cáo theo
`references/changes-template.md`, ghi vào Obsidian; nhiều service thì thêm một ghi chú tổng hợp. Khác `compare`: không hỏi "merge có an toàn không" mà hỏi "từ A đến B đã
đổi những gì".

```bash
D="${CLAUDE_PLUGIN_ROOT}/scripts"
T="${CLAUDE_PLUGIN_ROOT}/skills/changes/references/changes-template.md"
# rỗng thì: D="$(dirname "$(find ~/.claude -path '*code-review*/scripts/ref_changes.py' | head -1)")"
#           T="$(dirname "$D")/skills/changes/references/changes-template.md"
```

`$TMP` = scratchpad của session (hoặc `mktemp -d`); `OUT="$TMP/changes"`. Không ghi file vào repo.

## Bước 0 — Vault

```bash
python3 "$D/obsidian_note.py" check
```

Exit 3 (chưa cấu hình hoặc sai đường dẫn): dừng, nói người dùng thêm vào `~/.dev-cli/.env`:

```text
OBSIDIAN_VAULT=<thư mục vault, nơi có .obsidian/>
BRANCH_COMPARE_OBSIDIAN_DIR=code-changes        # tùy chọn, thư mục con trong vault
```

Chỉ khi người dùng đồng ý mới ghi tạm vào `review-reports/` của repo thay cho vault.

## Bước 1 — Phạm vi

- **Mốc**: `from` và `to` là tag, nhánh hoặc commit. Chỉ nói một mốc ("release v1.5 thay đổi gì"): `to` là
  mốc đó, `from=auto` (tag liền trước). Không nói gì: hỏi một câu.
- **Service**:
  - một service: repo đang đứng, hoặc repo người dùng nêu;
  - nhiều service: danh sách tên hoặc "tất cả trong <thư mục>":

    ```bash
    python3 "$D/ref_changes.py" --discover <thư mục> --from <from> --to <to> --format text
    ```

    Dòng `ok` có commit thì chạy. Dòng `skip` ghi vào tổng hợp kèm lý do; dòng `same` và dòng `0 commits` cũng ghi.
- **Stamp của lượt chạy** (`STAMP`), lấy một lần cho mọi service: `python3 "$D/obsidian_note.py" stamp`
  (dạng `yyyymmdd-HHMM`). Tên ghi chú: `<STAMP>-<from>~<to>.md` trong thư mục tên repo.

## Bước 2 — Fetch

Mỗi repo, chỉ cập nhật ref, không đụng working tree:

```bash
git -C <repo> fetch origin --tags --quiet [<nhánh from> <nhánh to>]
```

Fetch lỗi: dùng ref đang có, ghi vào tổng hợp.

## Bước 3 — Subagent mỗi service (song song)

Gọi `code-review:service-changes`, **một subagent cho một service, tất cả trong cùng một lượt** (quá 6
service thì chia đợt 6). Model: theo định nghĩa agent (Sonnet, effort high). Với service có diff rất lớn
(`range.added + range.removed` trên ~15 000 dòng trong `ref_changes.py --format text` chạy trước, hoặc một
commit đồng bộ/squash hàng chục nghìn dòng) gọi agent với `model: opus` (để effort mặc định của agent): đọc
được nhiều hơn trước khi kết luận, ít báo sai hơn. Người dùng chỉ định model/effort thì làm theo. Prompt cho
mỗi subagent:

```text
REPO=<đường dẫn tuyệt đối>
FROM=<from>  TO=<to>
SCRIPTS=<D tuyệt đối>
TEMPLATE=<T tuyệt đối>
OUT=<OUT tuyệt đối>
[DIALECT=<mariadb|postgresql|...> nếu người dùng nêu]
Viết báo cáo thay đổi của service này theo template.
```

Không tự làm phần việc của subagent khi nó đang chạy. Kết quả: dòng `OK <repo>` (đường dẫn `.md`, `.json`)
hoặc `FAILED <repo>: <lý do>`. Không có file `.md` thì coi như lỗi; không bịa nội dung thay.

Kiểm nhanh mỗi báo cáo trước khi ghi vault: đủ 8 mục, không có giá trị secret (`grep -nEi
'password|secret|token' <file>` chỉ được thấy tên key hoặc `<secret>`), số commit khớp JSON.

## Bước 4 — Tổng hợp đợt (nhiều service)

```bash
python3 "$D/ref_changes.py" --cross "$OUT"/*.json --format text > "$OUT/cross.md"
```

Viết `$OUT/summary.md` (không frontmatter; mục "Nghiệp vụ cần nghiệm thu" theo từng service do
`obsidian_note.py index` tự trích từ mục 1 của các báo cáo, không chép lại vào đây):

- 3–5 dòng cho cả đợt: nghiệp vụ cần nghiệm thu chéo service, việc phải làm;
- **Liên hệ giữa các service**: bảng `cross.md` (endpoint đổi có service khác gọi qua Feign; topic dùng
  chung), mỗi dòng thêm "cần làm";
- **Breaking và việc khi deploy** gộp theo service, thứ tự deploy nếu có phụ thuộc (bên cung cấp trước);
- **Không chạy được**: service `skip`/`FAILED` và lý do.

Một service: không viết tổng hợp (báo cáo service đã đủ).

## Bước 5 — Ghi vào Obsidian

Gọi `code-review:vault-publisher` (model Haiku):

```text
SCRIPTS=<D tuyệt đối>
STAMP=<stamp của lượt chạy>
REPORT=<OUT>/<repo>.md JSON=<OUT>/<repo>.json      (mỗi service một dòng)
[SUMMARY=<OUT>/summary.md                          (chỉ khi nhiều service)]
```

Subagent ghi từng ghi chú, ghi chú tổng hợp (nếu có), rồi chạy lệnh đồng bộ của vault trong Obsidian
(`obsidian_note.py sync`: CLI của Obsidian chạy lệnh `git-vault-sync:sync`, rồi chờ ghi chú được commit).
Obsidian chưa mở (exit 5): ghi chú vẫn nằm trong vault, báo người dùng mở Obsidian (plugin bật sync on
startup sẽ tự đồng bộ). Không tự chạy git trong vault.

Ghi chú nằm ở `<vault>/<BRANCH_COMPARE_OBSIDIAN_DIR>/<repo>/<STAMP>-<from>~<to>.md` (thư mục repo tạo mới
nếu chưa có); tổng hợp ở `<vault>/<BRANCH_COMPARE_OBSIDIAN_DIR>/_tong-hop/<STAMP>-<from>~<to>.md`.
Ghi chú service có properties `repo, from, to, commits, breaking, migrations, findings_high, tags` (Dataview
truy vấn được). Chạy lại cùng stamp thì ghi đè ghi chú của chính skill; ghi chú người khác viết thì không
(exit 4, báo lại).

## Bước 6 — Trả lời

Trong chat chỉ vài dòng: mỗi service một dòng (commit, breaking, migration, cảnh báo cao), liên hệ giữa
service quan trọng nhất, đường dẫn ghi chú tổng hợp, trạng thái đồng bộ (đã commit/đẩy, hoặc chưa và vì sao). Telegram chỉ khi người dùng yêu cầu (skill
`dev-tools:telegram`).

## Không làm

- Không checkout, merge, rebase, push, commit; không ghi gì vào repo; không build, không chạy test.
- Không in giá trị secret, token, URL có mật khẩu vào chat, báo cáo hay vault.
- Không kết luận từ cờ khi chưa đọc code; không đoán ngoài diff; không nhận xét người viết commit.
- Không xóa, đổi tên ghi chú trong vault; không ghi ra ngoài thư mục đã cấu hình.
