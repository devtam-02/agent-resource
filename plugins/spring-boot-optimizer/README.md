# spring-boot-optimizer

Claude Code plugin to review and optimize Java / Spring Boot services, tuned for
financial back ends (money, idempotency, transactions) and a SonarQube quality gate.

| Component | What it does |
|---|---|
| skill `optimize-spring-boot` | Scan → findings by severity → small fixes with tests → build + Sonar self-check → report. References: `finance-rules.md`, `performance.md`, `sonar.md`, `style.md`, `testing.md`. |
| agent `spring-boot-reviewer` | Read-only review of a diff or files, ranked findings with `file:line`. |
| command `/spring-boot-optimizer:optimize [path] [focus]` | Runs the skill on a path, or on the branch's changed files. |
| command `/spring-boot-optimizer:review [range]` | Runs the reviewer on a git range, then offers to fix. |
| hook (PostToolUse on Edit/Write) | After Claude edits a `.java` file, runs `java_scan.py` on it and feeds errors back (S125 `//…;`, unused/wildcard imports, `System.out`, money as `double`, `new BigDecimal(0.1)`, empty catch, `@Transactional` on private methods, ...). Silence with `SPRING_BOOT_OPTIMIZER_HOOK=off`. |

The scanner also works on its own:

```bash
python3 skills/optimize-spring-boot/scripts/java_scan.py src/            # human-readable, exit 1 on findings
python3 skills/optimize-spring-boot/scripts/java_scan.py --json src/     # JSON
```

It is a set of line-based heuristics (Python 3 standard library only): fast and cheap, not a
replacement for SonarQube or for reading the code. Secrets found in config files are reported
by key only; values are never printed.

Requirements: Python 3 on `PATH` (for the hook and the scanner).
