---
name: troubleshooting
description: Debug failing commands, scripts and system issues step by step.
when_to_use: whenever something errors, fails to build or run, or behaves unexpectedly
---

# Troubleshooting Procedure

1. Reproduce the failure exactly and capture the full error output.
2. Read the relevant file(s) with `read_file` before changing anything.
3. Form one hypothesis at a time and verify it cheaply (small tests).
4. Fix forward: make the smallest change that addresses the root cause.
5. Re-run to confirm the fix, then run a broader sanity check.
6. Report both what you ran and its exit code/output.
7. If three approaches failed, say so clearly and summarise what you ruled
   out so the user can help unblock you.