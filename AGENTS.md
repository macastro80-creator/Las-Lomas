# Agent Guidelines: Custom /pr-create Command

Whenever the user requests or types the command `/pr-create` (or says "create PR", "crear PR", or similar):

1. **Run the automation script:**
   Execute `/opt/homebrew/bin/python3 scripts/create_pr.py` using the `run_command` tool.
   
   > [!IMPORTANT]
   > You **must** set `BypassSandbox: true` when running this command. Bypassing the sandbox is required because the script needs network access to push the branch to GitHub and run the GitHub CLI (`gh`), as well as access the macOS keyring for credentials.

2. **Pass arguments if provided:**
   If the user specified a custom branch name, commit message, title, or body in their prompt, pass them to the script as arguments:
   - `--branch "<branch-name>"`
   - `--commit-msg "<commit-message>"`
   - `--title "<pr-title>"`
   - `--body "<pr-description>"`

3. **Parse output and respond:**
   - Wait for the script to complete.
   - If the script succeeds, it will print `PR_LINK: <url>` and `PR_NUMBER: <number>`. Parse these and return a concise, friendly response to the user containing the link to the created PR and its number, confirming that all tests passed successfully.
   - If the script fails, report the test failures or errors to the user.
