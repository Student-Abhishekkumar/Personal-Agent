---
name: code-review
description: Perform a structured, thorough code review of a file or snippet.
when_to_use: whenever the user asks to review, audit, or improve code
---

# Code Review Procedure

1. Read the target file with `read_file` (or use the provided snippet).
2. Identify issues in these categories:
   - correctness bugs and missing edge cases
   - security risks (injection, path traversal, secrets, unsafe eval/exec)
   - performance problems (unnecessary work, N+1 patterns, big allocations)
   - readability, naming, and maintainability
3. For every finding, report:
   - severity: critical / major / minor / nit
   - exact location (file:line)
   - why it is a problem
   - a concrete fix, with a short code snippet
4. End with 1-3 strengths and a suggested priority order for the fixes.
5. Keep tone constructive; respect the idioms of the codebase's language.