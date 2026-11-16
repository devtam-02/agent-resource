---
name: review-pr
description: >
  Review a pull request (Bitbucket Server/DC, or any two git branches) of a Spring Boot service, a
  front-end app (React/Next.js, Vue/Nuxt, Angular, Svelte) or a repository with both: for each commit,
  what it does; how far the change reaches (impact); config keys and database migrations (Flyway,
  Liquibase YAML/XML/SQL) compared with what each environment overrides; Kafka risks; for front-end,
  routes and screens, API calls, environment variables, dependencies and front-end security rules; and
  a deep line-by-line code review by the code-reviewer agent (Opus). Reads the PR (title, author, merge
  status, CI builds) from Bitbucket when configured, scans the git range locally, never checks out,
  merges, approves, comments or pushes. Writes the report from a fixed Markdown template and, only when
  asked, sends that Markdown file to Telegram through telegram-send. Use when the user asks to review a PR
  or a branch range, or says "review PR", "review pull request", "rà PR", "review PR #13", "review nhánh
  feature vào staging", "review code front-end", "PR này có migration không", "PR này đổi config gì",
  "gửi kết quả review lên telegram".
argument-hint: "<link PR | --pr <số> | --base <nhánh đích> --head <nhánh nguồn>> [--send-telegram]"
effort: medium
---

# Review PR (Spring Boot và front-end)

Ba câu hỏi bắt buộc, theo đúng thứ tự:

1. **Mỗi commit làm gì** — liệt kê theo commit, nhóm theo chủ đề.
2. **Ảnh hưởng đến đâu** — API, nghiệp vụ, dữ liệu, Kafka, runtime, rollback.
3. **Có đổi config hoặc database không** — key nào, môi trường nào ghi đè, migration nào, rủi ro gì.

Sau đó tới code: agent `code-review:code-reviewer` (Opus) đọc từng file thay đổi và tìm lỗi thật (Bước 3b).
Template: `references/report-template.md`. Checklist: `references/checklist.md`. Script của plugin:

```bash
D="${CLAUDE_PLUGIN_ROOT}/scripts"
# rỗng thì: D="$(dirname "$(find ~/.claude -path '*code-review*/scripts/pr_scan.py' | head -1)")"
```

Thư mục tạm cho JSON/patch: scratchpad của session (hoặc `mktemp -d`), **không ghi vào repo**.

## Bước 1 — Xác định PR và hai nhánh

- **Có link PR hoặc số PR** và Bitbucket đã cấu hình (`python3 "$D/bitbucket_pr.py" doctor` báo `auth: ok`):
  ```bash
  python3 "$D/bitbucket_pr.py" info "<link PR>" --format text      # hoặc: info --pr 13
  python3 "$D/bitbucket_pr.py" info "<link PR>" > "$TMP/pr.json"
  ```
  Lấy: tiêu đề, tác giả, nhánh nguồn/đích, commit đầu nguồn, `merge` (canMerge, conflicted, vetoes),
  `builds` (CI), reviewer. Chỉ GET; không approve, comment hay merge.
- **Chỉ có tên nhánh nguồn**: `bitbucket_pr.py find --source <nhánh> [--target <đích>]` để tìm số PR.
- **Bitbucket chưa cấu hình** (exit 3) hoặc người dùng chỉ đưa hai nhánh: làm tiếp với hai nhánh. Ô PR/CI
  trong báo cáo ghi "không có thông tin (chưa đọc Bitbucket)". Nhánh chưa rõ thì hỏi **một** câu, không đoán.
- Exit 4: PR hoặc repo không tồn tại. Exit 5: mạng/TLS; CA nội bộ thì khai `BITBUCKET_CA_BUNDLE`,
  **không bao giờ tắt kiểm tra chứng chỉ**.

## Bước 2 — Fetch và đối chiếu commit

```bash
git fetch origin <nhánh nguồn> <nhánh đích>     # chỉ cập nhật remote-tracking ref
```

Có `pr.json`: chạy lại `info` kèm `--repo .` và xem `local.matches`. `false` nghĩa là
`origin/<nguồn>` khác commit đầu của PR (có người vừa push) — fetch lại hoặc nói rõ đang review commit nào.

## Bước 3 — Quét git range

```bash
python3 "$D/pr_scan.py" --base origin/<đích> --head origin/<nguồn> [--pr <số>] \
  --patch-file "$TMP/pr.patch" > "$TMP/scan.json"
python3 "$D/pr_scan.py" --base origin/<đích> --head origin/<nguồn> [--pr <số>] --format text
```

`scan.json` có:
- `range`: commit, file, ±dòng, tác giả, ngày, `dialect` (DB tự nhận ra: mariadb/mysql/postgresql/...),
  `merge` (thử merge ở local: `clean` / `conflict` + file / `unknown`), `touches_build`, `report_name`.
- `commits`: từng commit, `categories`, `flags`.
- `findings` đã sắp theo mức: SQL theo DB, thao tác Liquibase, changelog master, Flyway (trùng/lệch
  phiên bản), migration cũ bị sửa, migration mới chưa được include, config (ddl-auto, actuator,
  secret, Kafka group/offset/topic, timeout, profile), Java (JPA, transaction, endpoint, Kafka listener,
  topic hardcode, xóa mềm, security).
- `config_changes`, `migrations`, `dependencies` (Maven/Gradle; `package.json` có phiên bản cũ → mới).
- `range.stack`: `spring`, `frontend`, `node` (một repo có thể có nhiều). `frontend`: `routes` (trang,
  màn hình), `api_clients`, `config` (next/vite/tsconfig…), `i18n`, `env_keys` (biến môi trường mới được
  đọc), `api_calls` (đường dẫn API thêm/bỏ). Cờ `fe-*`: XSS, `eval`, token ở web storage, biến public mang
  tên secret, URL viết cứng, `target=_blank` thiếu `rel`, guard đăng nhập thêm/bỏ, nâng major dependency.
- `review_files`: file nguồn đổi (không test, không file sinh), đổi nhiều trước — đầu vào của Bước 3b.

DB nhận sai thì chạy lại với `--dialect mariadb|postgresql|...`. Script không in giá trị secret.
Exit 2: đọc dòng lỗi (thường là ref chưa fetch).

## Bước 3b — Review code sâu (subagent, chạy song song)

Ngay sau Bước 3, nếu `review_files` không rỗng, gọi `code-review:code-reviewer` (model Opus, effort medium
theo định nghĩa agent) **ở chế độ nền**, rồi làm tiếp Bước 4–5 trong lúc chờ:

```text
REPO=<đường dẫn tuyệt đối của repo>
BASE=<range.merge_base>  HEAD=<range.head_sha>
STACK=<range.stack>
FILES=<review_files: đường dẫn, đổi nhiều trước; tối đa ~60 dòng>
INTENT=<tiêu đề + mô tả PR, hoặc subject các commit>
OUT=<$TMP tuyệt đối>/code-review.md
```

Không tự đọc lại cùng các file đó khi agent đang chạy. Khi agent xong: đọc `OUT`, **kiểm lại mỗi mục 🔴
trên code** (`git show <head>:<file>` quanh dòng được dẫn) và bỏ mục không có bằng chứng; ghi phần còn lại
vào mục 4 của báo cáo. Agent báo lỗi (thiếu đầu vào, không đọc được): ghi "chưa review code sâu" và lý do.

## Bước 4 — Đối chiếu cấu hình môi trường

Khi có `config_changes` ở file đóng gói (`application*.properties|yml`) hoặc có cờ Kafka:

```bash
python3 "$D/env_overrides.py" --service <service> --scan "$TMP/scan.json" \
  --keys spring.kafka.consumer.auto-offset-reset --format md
```

Kết quả là bảng key × staging/uat/production: "ghi đè: `giá trị`" hoặc "—" (dùng giá trị trong code).
Đây là bảng mục 3.2 của báo cáo. Từ đó kết luận:
- key đổi trong code mà **mọi môi trường đều ghi đè** → thay đổi không có tác dụng (nêu rõ);
- key mới không có default mà môi trường không có → app có thể không khởi động;
- group Kafka mới + môi trường đặt `auto-offset-reset=earliest` → đọc lại toàn bộ lịch sử topic (🔴).

Cần plugin app-config-review (`APP_CONFIG_*`). Không có: bỏ bảng, ghi "chưa đối chiếu cấu hình môi
trường". Tên service: thư mục trong repo cấu hình (`spring.application.name` hoặc tên repo).

Front-end: mỗi key trong `frontend.env_keys` phải có ở nơi cấu hình build/deploy (`.env.example`,
`.env.<môi trường>`, Helm values, Dockerfile `ARG`/`ENV`, pipeline). Biến `NEXT_PUBLIC_*`/`VITE_*`/
`REACT_APP_*` được đóng gói lúc build: đổi giá trị phải build lại, không chỉ restart.

## Bước 5 — Đọc có chọn lọc

Không đọc toàn bộ diff khi lớn. Ưu tiên:

1. **Mọi migration** trong `migrations`, đọc đủ file (`git show origin/<nguồn>:<file>`). Với changelog master:
   thứ tự include, include đã có có bị đổi không.
2. **File config** có thay đổi, cùng bảng ghi đè của bước 4.
3. **Commit có cờ** `high`/`medium`: `git show <sha> -- <file>`.
4. **Controller, entity, consumer Kafka** đổi: phần đổi (`git show -U5 <sha> -- <file>`).
5. Phía gọi API bị đổi hợp đồng (repo khác) chỉ khi người dùng có repo đó; không thì nêu "chưa kiểm client".
6. **Front-end**: `frontend.routes` (màn hình nào đổi, guard đăng nhập/quyền), `frontend.api_calls` và
   `api_clients` (đường dẫn, payload so với backend), `fe-config` (next/vite config, middleware), file
   `i18n` (key mới có đủ ở mọi ngôn ngữ không), `package.json` (major bump, thư viện mới).

Trên ~500 dòng diff: nói rõ review tập trung vào 1–4.
Không dùng `git checkout`, `stash`, `reset`, `merge`, `push`. Không build hay chạy test trừ khi người
dùng yêu cầu; nếu không chạy, ghi rõ trong báo cáo.

## Bước 6 — Viết báo cáo

Theo đúng `references/report-template.md`:

- **Đầu báo cáo**: bảng PR / Nhánh / Phạm vi / Tác giả / Merge / CI, rồi một đoạn tóm tắt.
  - *Merge*: từ Bitbucket (`merge`) nếu có, kèm kết quả thử merge local (`range.merge`); nêu
    `touches_build` (có đụng `pom.xml`/Gradle không).
  - *CI*: `builds.summary`; "không có build status nào" thì ghi ⚠️ chưa có bằng chứng build/test xanh.
- **1. Tác dụng của từng commit** — nhóm theo chủ đề, mỗi commit một dòng (1 câu, nói nghiệp vụ hoặc
  kỹ thuật, có mã yêu cầu nếu commit có). Không chép nguyên commit message.
- **2. Đánh giá ảnh hưởng** — mục theo mức 🔴 / 🟡 / 🟢 / ⚪. Mỗi 🔴 có "Cần làm trước khi deploy".
  Vấn đề code (bug, performance, convention) nằm ở đây, có `file:dòng` hoặc tên method.
- **3. Config và database migration** — 3.1 migration (bảng + chi tiết file rủi ro cao + câu SQL kiểm
  dữ liệu trước khi chạy), 3.2 config (diff key + bảng ghi đè theo môi trường), 3.3 front-end (màn hình,
  API gọi, biến môi trường, dependency), 3.4 việc ngoài repo (# / việc / nơi: Infra, DBA, Dev). Không có
  thì ghi rõ "Không có", không bỏ mục.
- **4. Review code** — bảng lỗi của Bước 3b đã kiểm lại (mức / file:dòng / vấn đề / hậu quả / đề xuất),
  kèm "Chưa đọc" và "Câu hỏi cho tác giả". Mục 🔴 ở đây cũng lên mục 2.
- **Kết luận** — đánh giá chung, Approve / Request Changes / Needs Discussion, các việc phải chốt.

Quy tắc chấm:

- 🔴 luôn: `fe-xss-html` hoặc `fe-public-secret` đã xác nhận trên code, `migration-existing-changed`, `lb-include-changed`, `flyway-duplicate-version`,
  `kafka-replay-risk` (hoặc group mới + môi trường đặt earliest theo bước 4), `ddl-auto` create,
  `actuator-exposed`, secret viết thẳng đã xác nhận, xung đột merge.
- `DROP`/`TRUNCATE`/`RENAME`/đổi kiểu cột/`dropTable`/`dropColumn`/`renameColumn`/`modifyDataType`:
  🔴 hoặc 🟡 kèm điều kiện kiểm; xác nhận bằng đọc câu lệnh, cờ chỉ là gợi ý.
- UNIQUE mới, NOT NULL mới, cột sinh `PERSISTENT`, index trên bảng lớn: 🟡, kèm câu SQL kiểm dữ liệu
  trùng/NULL và ghi chú cửa sổ bảo trì.
- Cờ `medium` khác: kiểm rồi mới xếp; không có bằng chứng sai thì không báo như lỗi.
- Mỗi kết luận có bằng chứng: file, dòng, tên key, tên bảng, tên topic. Không đoán ngoài diff.
- Giọng xây dựng, nói về code, không nói về người viết.

**Lưu file**: `review-reports/<range.report_name>` trong repo (hoặc thư mục người dùng chỉ định). Đây là
file duy nhất skill ghi vào repo; không `git add`, không commit. Báo đường dẫn đầy đủ. File trong `$TMP`
không cần giữ.

## Bước 7 — Gửi Telegram (chỉ khi người dùng yêu cầu)

Không tự gửi. Chỉ khi người dùng nói "gửi telegram", "gửi kết quả", "gửi báo cáo", hoặc truyền
`--send-telegram`: gửi **chính file báo cáo `.md`** của Bước 6, không soạn tin nhắn tóm tắt.

1. Báo cáo phải đã lưu ra đĩa (`review-reports/<range.report_name>`, đường dẫn tuyệt đối). Trước khi gửi,
   kiểm file không chứa giá trị secret, token hay URL có mật khẩu (`grep -nEi 'password|secret|token|://[^/ ]+:[^@ ]+@'`
   chỉ được thấy tên key hoặc `<secret>`); có thì sửa báo cáo trước.
2. Gửi đúng một lần qua skill `telegram-send:send`:
   ```bash
   python3 "<đường dẫn telegram-send>/skills/send/scripts/send_telegram.py" send "<đường dẫn tuyệt đối file .md>" --yes
   ```
   thêm `--topic <id>` nếu người dùng chỉ định chủ đề; hoặc gọi subagent `telegram-send:telegram-sender` với
   đường dẫn tuyệt đối của file và câu "người dùng yêu cầu gửi file này". Đường dẫn telegram-send:
   `${CLAUDE_PLUGIN_ROOT}/../telegram-send` hoặc `find ~/.claude/plugins -path '*telegram-send*/scripts/send_telegram.py' | head -1`.
3. "Đã gửi" chỉ khi lệnh thoát 0 và in `sent.`. Bị từ chối (tên file giống file bí mật, quá 50 MB…) thì
   không dùng `--force`; báo lý do và hỏi lại.
4. Gửi thêm tin nhắn tóm tắt chỉ khi người dùng nói rõ muốn cả tin nhắn.

## Không làm

- Không checkout, merge, rebase, push; không approve, comment, decline hay merge PR trên Bitbucket.
- Không in token, giá trị secret hay URL có mật khẩu vào chat, file báo cáo, hay Telegram.
- Không tắt kiểm tra TLS khi gọi Bitbucket.
- Không kết luận từ cờ khi chưa đọc câu lệnh/đoạn code.
- Không bỏ mục Config hay Database; trống thì ghi "Không có".
- Không nhận xét về người viết commit.
