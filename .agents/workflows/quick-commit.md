---
description: Quick commit and push without running tests
---

// turbo-all

1. Stage all changes.

```bash
cd ~/backup-teams && git add -A
```

2. Check what changed and build a commit message from it. Use `git diff --cached --stat` to inspect the staged files, then compose a short imperative-mood summary (max 72 chars) describing what was actually changed. Do not use generic messages like "update files".

```bash
cd ~/backup-teams && git diff --cached --stat
```

3. Commit with the message you composed in step 2.

```bash
cd ~/backup-teams && git commit -m "<your generated message here>"
```

4. Push to origin main.

```bash
cd ~/backup-teams && git push origin main
```

5. Report what was committed and confirm the push succeeded.
