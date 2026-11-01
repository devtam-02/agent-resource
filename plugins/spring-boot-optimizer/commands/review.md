---
description: Review Spring Boot changes (read-only) with the spring-boot-reviewer subagent
argument-hint: "[git range | files]  e.g. origin/main..HEAD"
---

Delegate to the `spring-boot-reviewer` subagent to review: $ARGUMENTS

With no argument, review uncommitted changes plus commits not yet on the base branch.
Show its findings as returned, most severe first, then ask whether to fix them with the
`optimize-spring-boot` skill. Do not edit anything before the user answers.
