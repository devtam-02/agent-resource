---
description: Optimize Spring Boot code in a path (default - files changed on this branch) with the optimize-spring-boot skill
argument-hint: "[file|folder] [focus: perf|money|sonar|tests]"
---

Use the `optimize-spring-boot` skill on: $ARGUMENTS

If no path is given, work on the files changed on the current branch (uncommitted plus
commits not on the base branch). If a focus is given, check that area first, but still report
any critical finding elsewhere in scope. Follow the skill's process: scan, list findings with
severity, ask before schema/contract/dependency changes, fix in small steps with tests, run the
build and the Sonar self-check, then report.
