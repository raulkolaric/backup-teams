---
description: run tests, stage all changes, commit with an auto-generated message, and push to origin
---

// turbo-all

1. Run the full test suite and confirm it passes. Stop and report if any test fails — do not commit broken code.

```bash
cd ~/backup-teams && source venv/bin/activate && pytest tests/ -v 2>&1
```

2. Stage all changes.

```bash
cd ~/backup-teams && git add -A
```

3. Check what changed and build a commit message from it. Use `git diff --cached --stat` to inspect the staged files, then compose a short imperative-mood summary (max 72 chars) describing what was actually changed. Do not use generic messages like "update files".

```bash
cd ~/backup-teams && git diff --cached --stat
```

4. Commit with the message you composed in step 3.

```bash
cd ~/backup-teams && git commit -m "<your generated message here>"
```

5. Push to origin main.

```bash
cd ~/backup-teams && git push origin main
```

6. Report what was committed and confirm the push succeeded.
