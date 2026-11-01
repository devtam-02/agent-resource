---
name: review
description: Review Spring Boot changes read-only with the spring-boot-reviewer subagent, then offer to fix the findings with the optimize skill.
argument-hint: "[git range | files]  e.g. origin/main..HEAD"
disable-model-invocation: true
---

# Review Spring Boot changes

Phạm vi: `$ARGUMENTS`

1. Không có tham số → thay đổi chưa commit cộng các commit chưa có trên nhánh gốc
   (`git merge-base HEAD origin/HEAD`).
2. Giao cho subagent `spring-boot-reviewer` của plugin này review phạm vi đó. Subagent chỉ đọc,
   không sửa file.
3. Hiện nguyên các phát hiện nó trả về, nặng nhất trước (`critical`, `major`, `minor`, kèm `file:line`).
4. Hỏi người dùng có muốn sửa bằng skill `optimize` không. **Không sửa gì trước khi người dùng trả lời.**
