# Step 1: Mac Environment Preparation and Git Basics

This guide walks you through verifying that your Mac is ready to build the SenseCAP Watcher firmware and that Git is configured so we can collaborate smoothly. Complete these steps before moving on to firmware customization.

## 1. Confirm Required Tools

1. Open the **Terminal** app on your Mac.
2. Verify that the Xcode command line tools (which include Git) are installed:
   ```bash
   xcode-select --install
   ```
   * If the tools are already installed, the command will report that fact and exit.
   * If they are not installed, follow the prompts to install them.
3. Confirm Git is available:
   ```bash
   git --version
   ```
   You should see a version number printed.
4. Ensure Python 3 is installed (required for ESP-IDF scripts):
   ```bash
   python3 --version
   ```
5. Verify that the ESP-IDF environment is present at `~/esp/esp-idf`:
   ```bash
   ls ~/esp/esp-idf
   ```
   This should list the contents of the ESP-IDF directory.

## 2. Configure Git Identity

1. Set your global Git username (replace the example name with your own):
   ```bash
   git config --global user.name "Your Name"
   ```
2. Set your global Git email:
   ```bash
   git config --global user.email "you@example.com"
   ```
3. Confirm the configuration:
   ```bash
   git config --global --list
   ```

## 3. Configure SSH Access to Git

1. Check whether you already have an SSH key:
   ```bash
   ls ~/.ssh/id_*.pub
   ```
   * If you see a file such as `id_rsa.pub` or `id_ed25519.pub`, you already have a public key.
2. If you do not have a key yet, generate a modern Ed25519 key (press **Enter** to accept the defaults and optionally set a passphrase):
   ```bash
   ssh-keygen -t ed25519 -C "you@example.com"
   ```
3. Copy the public key to your clipboard so you can add it to your Git hosting service (for GitHub, paste it into **Settings → SSH and GPG keys → New SSH key**):
   ```bash
   pbcopy < ~/.ssh/id_ed25519.pub
   ```
   *If `pbcopy` is unavailable, open the file in a text editor and copy it manually.*
4. Test that SSH access to your Git host works (replace `github.com` if you are using a different provider):
   ```bash
   ssh -T git@github.com
   ```
   You should see a greeting confirming successful authentication. If prompted with *"Are you sure you want to continue connecting"*, answer `yes`.

## 4. Clone the Firmware Repository with Submodules

1. Create (or confirm) your development directory:
   ```bash
   mkdir -p ~/dev
   cd ~/dev
   ```
2. Clone the repository and automatically pull its submodules using the **SSH** URL (this avoids username/password prompts when pushing):
   ```bash
   git clone --recurse-submodules git@github.com:<YOUR_USERNAME>/<REPO_NAME>.git
   ```
   Replace `<YOUR_USERNAME>` and `<REPO_NAME>` with your GitHub account and repository name.
3. Change into the project directory:
   ```bash
   cd SenseCAP-Watcher-Firmware
   ```
   Confirm you are on the shared collaboration branch:
   ```bash
   git switch feature/pumpkin-server
   ```
   If Git reports that the branch does not exist locally, create it from the remote copy:
   ```bash
   git switch -c feature/pumpkin-server origin/feature/pumpkin-server
   ```
   Verify the branch list so you can see both your local branch (`* feature/pumpkin-server`)
   and the remote tracking branch:
   ```bash
   git branch -a
   ```
4. Verify the submodules were initialized:
   ```bash
   git submodule status
   ```
5. Double-check that the `origin` remote is using SSH so later pushes will work without a password prompt:
   ```bash
   git remote -v
   ```
   The URL should start with `git@github.com:`. If it shows an `https://` URL, switch it to SSH:
   ```bash
   git remote set-url origin git@github.com:<YOUR_USERNAME>/<REPO_NAME>.git
   ```

## 5. Activate the ESP-IDF Environment

1. From your Terminal (while still on the Mac), source the ESP-IDF export script:
   ```bash
   source ~/esp/esp-idf/export.sh
   ```
   *Do this in every new terminal session before building.*
2. Confirm the environment is active by checking that `idf.py` is on your PATH:
   ```bash
   which idf.py
   ```
   The command should return a path inside the ESP-IDF directory.

## 6. Practice Basic Git Commands

1. Check the repository status:
   ```bash
   git status
   ```
2. Create a new branch for experimentation:
   ```bash
   git checkout -b practice/setup-check
   ```
3. Make a trivial change (for example, create an empty file) and stage it:
   ```bash
   touch practice.txt
   git add practice.txt
   ```
4. Commit the change:
   ```bash
   git commit -m "Practice commit: verify git workflow"
   ```
5. View recent history:
   ```bash
   git log --oneline --graph -5
   ```
6. Remove the practice file and branch when finished:
   ```bash
   git checkout main
   git branch -D practice/setup-check
   rm practice.txt
   git checkout -- practice.txt 2>/dev/null || true
   ```

Once you complete these steps, your Mac environment and Git workflow should be ready for building and modifying the SenseCAP Watcher firmware. Let me know when you’re done or if you encounter any issues.

## 7. Syncing Updates from the Assistant (Super Simple Version)

Think of this like tidying up your toys before you bring in new ones. These steps make sure your computer has the same files that I just changed in the Git repository. I really am editing the real repo you cloned; pulling will copy those commits onto your Mac.

1. **Go to the project folder.**

   ```bash
   cd ~/dev/SenseCAP-Watcher-Firmware
   ```

   (Use the path where you actually cloned the repo if it is different.)

2. **See if you have messy toys on the floor (changes).**

   ```bash
   git status
   ```

   * If Git prints `nothing to commit, working tree clean`, you are already tidy.
   * If you see files listed in red or green, you must decide what to do with them:

     *Keep the work for later:*

     ```bash
     git stash push -m "pause before pulling"
     ```

     This puts your work in a temporary box. You can get it back later with `git stash pop`.

     *Throw the work away:*

     ```bash
     git reset --hard HEAD
     git clean -fd
     ```

     `git reset --hard` forgets changes to tracked files. `git clean -fd` only removes extra files that are not tracked—if it only deletes one file, that simply means there was just one extra toy on the floor. If nothing disappears, that is okay too.

3. **Ask Git which branch you are standing on.**

   ```bash
   git branch --show-current
   ```

  * If the answer is `feature/pumpkin-server`, stay there. That is the branch I am updating for you.
  * If you see `main` (or something else) and you want the shared branch named `feature/pumpkin-server`, run:

     ```bash
      git fetch origin
      git branch -a       # shows all branches; look for origin/feature/pumpkin-server
     ```

     * If you see `remotes/origin/feature/pumpkin-server`, hop onto it:

       ```bash
       git checkout feature/pumpkin-server  # if it already exists locally
       ```

       or, if Git says the branch is missing locally:

       ```bash
       git checkout -b feature/pumpkin-server origin/feature/pumpkin-server
       ```

     * If there is no `origin/feature/pumpkin-server`, it means we are collaborating on another branch (maybe `main`). Use that name in the next step instead.

4. **Pull the new commits down the SSH pipe.** This copies the updates I pushed into your clone.

   ```bash
   git pull --rebase origin $(git branch --show-current)
   ```

   The `--rebase` flag keeps your history neat. If Git asks you to resolve conflicts, stop and let me know what you see.

5. **Peek at what changed.**

   ```bash
   git log --oneline -3
   git status
   ```

   You can also open files directly (for example, `open docs/mac_setup_and_git.md`) to read the fresh instructions.

6. **(Optional) Bring back your stashed work.** Only do this if you ran the stash command earlier.

   ```bash
   git stash pop
   ```

   Fix any merge prompts that appear, then continue with your edits.

Follow this tidy-up routine each time I say new commits are available. It will keep your files matched with the repository I am updating for you.

### Optional: remove extra local branches

If you ended up with other local branches (for example `feature/sentient-pumpkin`) and want to keep only the shared branch and
`main`, list them first:

```bash
git branch
```

To delete a branch you no longer need (while you are **not** on it), run:

```bash
git branch -D feature/sentient-pumpkin
```

Repeat for any other unwanted branch names. This does not touch the copies stored on GitHub; it only cleans up your local list.

## Troubleshooting: Resetting a Project Build Directory

If a previous build configured the wrong ESP-IDF target (for example, running `idf.py set-target esp32` instead of `esp32s3`),
the `openai-realtime` example will fail during CMake with an error similar to:

```
ERROR: Because project depends on sensecap-watcher (*) which doesn't match any versions, version solving failed.
```

To fix the issue:

1. Remove the stale build artifacts and configuration files:
   ```bash
   rm -rf build sdkconfig sdkconfig.old
   ```
2. Re-run the target selection with the correct chip:
   ```bash
   idf.py set-target esp32s3
   ```

After the target is set to `esp32s3`, subsequent `idf.py build` invocations should succeed.
