# GitHub access to css-catalog-client from 192.10.0.7

npd-loader depends on `css-catalog`, which pip installs from the private repo
`https://github.com/Consumers-Checkbook/css-catalog-client`, pinned to a tag:

```
css-catalog @ git+https://github.com/Consumers-Checkbook/css-catalog-client@v0.1.0#subdirectory=python
```

pip runs `git` to fetch it, so the server needs a read-only credential for that one repository. This page sets
up a fine-grained personal access token (PAT) that git uses only for that repository.

The token goes on the **account that runs `pip install`** for npd-loader (on 192.10.0.7 that is `cbadmin`, which owns the install in `/home/cbadmin/national_provider_dir`). The
`npd` service account that runs the loader does not need it. Do the setup and every install as that same account.
Don't mix in `sudo pip`, because that reads root's git configuration instead.

## 1. Create the token (on github.com)

1. GitHub → your avatar → **Settings** → **Developer settings** → **Personal access tokens** →
   **Fine-grained tokens** → **Generate new token**.
2. Fill in:
   - **Token name:** `npd-loader 192.10.0.7 css-catalog read`
   - **Resource owner:** `Consumers-Checkbook`. The repository belongs to the organization, not to you.
   - **Expiration:** the longest the organization allows (for example 1 year). Put the expiry date in your
     calendar.
   - **Repository access:** *Only select repositories* → `css-catalog-client`.
   - **Permissions → Repository permissions → Contents:** *Read-only*. GitHub adds *Metadata: Read-only* by
     itself. Leave everything else at *No access*.
3. **Generate token** and copy it. GitHub shows it only once.
4. If the organization requires approval for fine-grained tokens, the token shows as *pending*. An owner of
   `Consumers-Checkbook` approves it under **Organization settings → Personal access tokens → Pending requests**.
   Until then, step 3 below fails with a 403 or "not found" error.

## 2. Store it on 192.10.0.7

Log in as cbadmin. These commands tell git to use the stored credential **only** for the
css-catalog-client URL, so other GitHub clones on the server, such as npd_fhir_loader itself, are unaffected.

```bash
REPO=https://github.com/Consumers-Checkbook/css-catalog-client
git config --global "credential.$REPO.helper" store
git config --global "credential.$REPO.useHttpPath" true

read -rs TOKEN            # paste the token, press Enter (nothing is echoed or kept in history)
umask 077
printf 'https://x-access-token:%s@github.com/Consumers-Checkbook/css-catalog-client\n' "$TOKEN" >> ~/.git-credentials
unset TOKEN
chmod 600 ~/.git-credentials
```

Never put the token in `pyproject.toml`, `config.toml`, a requirements file or the repository.

## 3. Check it works

```bash
GIT_TERMINAL_PROMPT=0 git ls-remote https://github.com/Consumers-Checkbook/css-catalog-client refs/tags/v0.1.0
```

Expected: one line with a commit hash and `refs/tags/v0.1.0`. pip uses this same URL, so if this works, the
install will too.

| Output | Meaning |
|---|---|
| `terminal prompts disabled` / `Authentication failed` | git did not find the credential: check the URL in `~/.git-credentials` and the two `git config` lines, and that you are logged in as cbadmin |
| `403` or `Repository not found` | the token was found but has no access: it is still pending approval, scoped to the wrong owner or repository, or expired |
| no output, exit 0 | access works but the tag `v0.1.0` has not been pushed yet |

## Renewing or revoking

- **Renew before expiry:** create a new token as in step 1. In `~/.git-credentials`, replace the old token on the
  `css-catalog-client` line. Run step 3. Then delete the old token on GitHub.
- **Revoke:** delete the token on GitHub (Settings → Developer settings → Fine-grained tokens) and remove its line
  from `~/.git-credentials`.
- Installs that are already done keep working without the token. Only new installs and upgrades need it.

A PAT belongs to the person who created it. If that person loses access to `Consumers-Checkbook` or leaves, the
token stops working. If that becomes a problem, use a read-only **deploy key** on the repository instead
(Repository → Settings → Deploy keys). It isn't tied to a person and doesn't expire, but git on the server then
needs an SSH key and an `insteadOf` rule that rewrites the https URL to `git@github.com:`.
