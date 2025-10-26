# Server Module: Step-by-Step Git Workflow

Follow these commands on your Mac to create the pumpkin server directory structure and add it to the SenseCAP Watcher firmware repository. Each step assumes you are inside the repo clone at `~/dev/SenseCAP-Watcher-Firmware` and have already completed the Git setup from [Step 1](./mac_setup_and_git.md).

## 1. Create a feature branch
Run these commands from the repository root (`~/dev/SenseCAP-Watcher-Firmware`).

```bash
git status
git fetch origin
git switch feature/pumpkin-server        # if Git says the branch is missing locally, run: git checkout -b feature/pumpkin-server origin/feature/pumpkin-server
```
* `git status` confirms you have no stray changes before branching.
* `git fetch origin` makes sure you know about the latest remote branches.
* `git switch feature/pumpkin-server` moves you onto our shared branch. The inline note shows the command to create it from the remote if needed.

## 2. Make the directory skeleton
Stay in the repository root for all `mkdir` commands unless noted otherwise.

```bash
mkdir -p server/config
```
The `-p` flag creates both the `server/` folder (if it does not yet exist) and the nested `config/` folder in one shot.

## 3. Add starter files
Create the placeholder files that document and configure the server. You can use any editor; the commands below are quick Terminal one-liners that match the files in this repository. Run each of them from the repository root so the paths resolve correctly.

```bash
cat <<'EORT' > server/README.md
# SenseCAP Pumpkin Server

(Write a short overview of the Mac-side helper that will handle speech-to-speech calls and animation cues.)
EORT

cat <<'EOCONF' > server/config/persona.example.yaml
# Example persona configuration used for development
role: Friendly Pumpkin
objective: Make trick-or-treaters laugh while keeping conversations short.
voice: openai/voice/pumpkin_fun
EOCONF

cp server/config/persona.example.yaml server/config/persona.yaml

touch server/main.py
cat <<'EOREQ' > server/requirements.txt
openai>=1.35.0
websockets>=14,<15
pydantic>=2.6
PyYAML>=6.0
soundfile>=0.12
numpy>=1.26
pytest>=8.1
pytest-asyncio>=0.23
EOREQ
```
* `cat <<'EOF' > file` writes multi-line text into a new file.
* `cp` duplicates the example persona so you have an editable copy.
* `touch` creates an empty `main.py` that we will fill in later.

If you need additional folders (for example `server/certs/` or `server/tests/`), create them now with `mkdir` and add placeholder `.gitkeep` files. Execute these from the repository root as well:
```bash
mkdir -p server/certs server/tests
touch server/certs/.gitkeep server/tests/__init__.py
```

## 4. Review your work
While still in the repository root, verify the folders and files were created:

```bash
ls server
ls server/config
```
Confirm the files exist and look correct. Open them in your editor to make sure the contents are what you expect.

## 5. Stage the new files
Stay at the repository root for the Git commands in this section.

```bash
git status
git add server
```
* `git add server` stages everything inside the `server/` directory for commit.

If you created other support files (such as documentation under `docs/`), stage them as well from the repository root:
```bash
git add docs/server_module_setup.md
```

## 6. Inspect the staged diff
Run the inspection commands from the repository root:

```bash
git status
git diff --staged
```
Read through the output so you know exactly what will be committed. If you spot a mistake, edit the file, re-run `git add`, and check the diff again.

## 7. Commit
Commit from the repository root so Git finds the `.git` metadata:

```bash
git commit -m "feat: add pumpkin server scaffold"
```
Pick a descriptive message that summarises the change.

## 8. Push (optional, when you are ready to share)
Remain in the repository root when pushing to GitHub. Before the first push, confirm your `origin` remote uses the SSH URL so Git will not prompt for a username/password:

```bash
git remote -v
```

If you see an `https://` URL, switch it to SSH (replace the placeholder values with your own account and repository name):

```bash
git remote set-url origin git@github.com:<YOUR_USERNAME>/<REPO_NAME>.git
```

Now push the branch:

```bash
git push -u origin feature/pumpkin-server
```

The `-u` flag remembers the upstream branch so future `git push` calls work without extra arguments.

## 9. Keep iterating
Whenever you modify the server code:
1. Edit the files.
2. Run `git status` to see what changed.
3. Stage with `git add <files>`.
4. Commit with a meaningful message.
5. Push the branch when you want feedback or are ready to open a pull request.

By repeating this workflow, you will build up the pumpkin server module step by step while keeping Git history clean and easy to follow.
