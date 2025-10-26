# How We Work Together

> Written so it makes sense to a 6th grader. 😊

## Big Idea
Inside this Codex space I can edit files and make commits, but I cannot log in to GitHub or push those commits anywhere. Think of this as a **scratchpad copy** of your fork. When you want those changes on your laptop, you copy the patches or file contents I show you and apply them locally.

> ✅ You are the only one who can push to your GitHub fork. My commits stay in this workspace unless you copy them out. That is why you do not see new commits appear automatically on GitHub.

## Your Daily Checklist
1. **Open the project folder.**
   ```bash
   cd ~/dev/SenseCAP-Watcher-Firmware
   ```
2. **Make sure you are in the shared branch.**
   ```bash
   git status
   git switch feature/pumpkin-server
   ```
3. **Grab my latest work.**
   ```bash
   git pull --rebase origin feature/pumpkin-server
   ```
4. **Do your changes.**
   * Edit files, run builds, run tests.
5. **See what changed.**
   ```bash
   git status
   git diff
   ```
6. **Save your work.**
   ```bash
   git add <files you touched>
   git commit -m "write a short message"
   ```
7. **Send it to me.**
   ```bash
   git push origin feature/pumpkin-server
   ```
8. **Tell me what happened.**
   * Share build/test results or errors, and I will help fix things.

## How to Take My Changes to Your Laptop

Here is the exact play-by-play every time I hand you new code:

1. **I will say which files changed.** I will usually give you either:
   * A list of whole files ("open `server/main.py` and replace it with…"), or
   * A `git diff` snippet that shows the before/after lines.
2. **Open the file on your laptop.** Stay inside the project root (`~/dev/SenseCAP-Watcher-Firmware`). Use your editor of choice (`nano`, `code`, etc.). Example for `nano`:
   ```bash
   nano server/main.py
   ```
3. **Paste my version in.** If I supplied the entire file, highlight everything in your editor, delete it, and paste the new content. If I supplied a diff, carefully edit the lines so the `-` lines disappear and the `+` lines appear.
4. **Save and exit your editor.** In `nano`, press `Ctrl+O`, `Enter`, then `Ctrl+X`.
5. **Check that Git sees the change.**
   ```bash
   git status
   ```
   You should see the file listed in red under “Changes not staged for commit.”
6. **Repeat for each file** I mentioned.
7. **Double-check everything.**
   ```bash
   git diff
   ```
   This shows exactly what changed. Compare it to the diff I provided to make sure nothing was missed.
8. **Only after you are happy, run tests/builds.** This proves the pasted code matches what I have.

### Bonus: Using a Diff File Instead of Copy-Paste

Sometimes I will paste a big diff. You can save it to a file and let Git apply it:

1. Copy the diff text from the chat and paste it into a temporary file:
   ```bash
   nano /tmp/update.diff
   # paste the diff
   # Ctrl+O, Enter, Ctrl+X to save
   ```
2. Apply it from the project root:
   ```bash
   git apply /tmp/update.diff
   ```
3. Run `git status` and `git diff` to confirm the changes landed.

If `git apply` prints an error, stop and let me know. We will walk through it together.

### Concrete Example (Copying One of My Updates)

Sometimes it helps to see the whole flow with real commands. Imagine I tell you that two
files changed: `server/main.py` and `server/tests/test_server.py`.

1. **Open the first file for editing.**
   ```bash
   cd ~/dev/SenseCAP-Watcher-Firmware
   nano server/main.py
   ```
   Paste the updated contents I provided, then save and exit (`Ctrl+O`, `Enter`, `Ctrl+X`).

2. **Edit the second file the same way.**
   ```bash
   nano server/tests/test_server.py
   ```
   Paste, save, and exit.

3. **Verify Git sees both edits.**
   ```bash
   git status
   ```
   The two files should appear under “Changes not staged for commit.”

4. **Review the diffs to be sure they match what I sent.**
   ```bash
   git diff
   ```
   If something looks different, reopen the file and fix it now.

5. **Run the tests I mentioned.** (For the server this is usually:)
   ```bash
   cd ~/dev/SenseCAP-Watcher-Firmware
   pytest server/tests
   ```
   Share the output with me so I know everything succeeded.

6. **Stage and commit the files.**
   ```bash
   git add server/main.py server/tests/test_server.py
   git commit -m "server: sync with codex changes"
   ```

7. **Push the commit to GitHub.**
   ```bash
   git push origin feature/pumpkin-server
   ```

8. **Confirm on GitHub.** Open your repository in the browser, switch to the
   `feature/pumpkin-server` branch, and verify the new commit appears with the
   exact message you used in step 6.

Once you see the commit online, I know we are looking at the same code and can keep
building from there.

## What If Git Complains?
- **Unstaged changes in the way?**
  ```bash
  git stash push -m "saving before pull"
  git pull --rebase origin feature/pumpkin-server
  git stash pop   # bring your edits back
  ```
- **You want a totally clean slate?**
  ```bash
  git reset --hard HEAD
  git clean -fd
  ```

## Starting Over From Scratch (When Things Get Really Messy)
Sometimes it feels easier to wipe the slate clean. Here is the safe way:

1. **Close any programs using the folder.**
2. **Move up one level so you are not inside the project.**
   ```bash
   cd ~/dev
   ```
3. **Rename the old folder so nothing important gets lost.**
   ```bash
   mv SenseCAP-Watcher-Firmware SenseCAP-Watcher-Firmware-backup
   ```
4. **Grab a fresh copy using the shared branch.**
   ```bash
   # Replace YOUR-USERNAME with your actual GitHub account name.
   git clone --recurse-submodules git@github.com:YOUR-USERNAME/SenseCAP-Watcher-Firmware.git
   cd SenseCAP-Watcher-Firmware
   git remote -v   # double-check it points at your GitHub account
   git switch feature/pumpkin-server
   git pull --rebase origin feature/pumpkin-server
   ```
5. **Delete the backup later** (after you are sure the new copy works):
   ```bash
   rm -rf ~/dev/SenseCAP-Watcher-Firmware-backup
   ```

Now your laptop matches exactly what I have.

## How to Peek at My Recent Work (So You Know I Really Did It)

Even though I cannot push to GitHub, I will always make commits inside this workspace. When I tell you I made a commit, you can prove it to yourself like this:

1. **Ask me for the commit message or hash.** I will include it in my summary (for example: `commit abc1234 - docs: update workflow instructions`).
2. **After you paste my changes into your local repo, run:**
   ```bash
   git status
   ```
   Make sure all the files I mentioned now appear as modified.
3. **Stage and commit the changes locally:**
   ```bash
   git add <each file>
   git commit -m "docs: update workflow instructions"
   ```
4. **Push the commit to GitHub:**
   ```bash
   git push origin feature/pumpkin-server
   ```
5. **Verify on GitHub:** open your fork in a browser, switch to the `feature/pumpkin-server` branch, and check the commit list. You should see the commit you just pushed (same message I told you).

Now both of us can see that the change made it to GitHub.

## How I Help You
- I write or change files and commit them here.
- I share the file contents or diffs with you.
- You paste/apply them locally, run tests, and push the commit to GitHub.
- Once it is on GitHub, tell me the commit hash or push output so I know it succeeded. I will then continue from that exact commit.

## Team Rules
- We both stay on the `feature/pumpkin-server` branch unless we agree to make a new one.
- Always pull before you start working each day.
- Always push after you commit so the other person sees it.
- Ask for help if Git says something you do not understand.

High five! 👋
