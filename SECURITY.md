# Security

This repository holds the credentials and the workflow that publish your app to Google Play and App Store Connect. Treat any weakness here as a production incident risk.

## Supported versions

Only the latest release (tag) of this template gets fixes. Copies of the template are maintained by their owners: pull fixes from the latest release into your copy.

## Reporting a problem

- **Do not** open an issue, PR or discussion that describes the weakness, and never paste a secret, token, key file or log containing one anywhere.
- Report it privately with GitHub's private vulnerability reporting: the repository's **Security** tab → **Report a vulnerability** (this opens a draft security advisory only the maintainers can see). Repository owners: enable it in **Settings → Code security → Private vulnerability reporting**. If you are working on your own copy of this template, report it to whoever maintains that copy. Include what you found, where (file and line, or workflow run), and whether you think a credential is exposed.
- **If a secret may have leaked, rotate it first, report second.** The procedures for Google Play, App Store Connect and LifeTime are in [docs/SETUP.md](docs/SETUP.md).
- This is a community project: reports are handled on a best-effort basis, with no response-time commitment.

## What we protect

| Asset | Why it matters |
|---|---|
| `GOOGLE_PLAY_JSON_KEY` | Can release the app to Google Play, including production |
| `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8` | App Store Connect API key; see [docs/SETUP.md](docs/SETUP.md) for its role and scope |
| `LIFETIME_API_TOKEN` | Reads environments, applications and native binaries from LifeTime |
| The release itself | A wrong or tampered binary, or an unintended release, reaches real customers |

## Threat model (summary)

| Threat | Mitigation in this repository |
|---|---|
| Someone releases by mistake | Manual trigger only; production paths run in the `store-production` environment; `dry_run`; one release at a time (`concurrency`) |
| Wrong app or wrong build uploaded | Package / bundle ID read from the binary must equal `config/app.json`; the app is fixed (no app input) |
| iOS build with the wrong signing | `fetch` rejects anything that isn't an App Store distribution profile (any profile with `get-task-allow` counts as development) and rejects expired profiles |
| LifeTime token sent to another host | Token only sent to the LifeTime host, over HTTPS, never on redirects |
| Secret exfiltration through a workflow step | Secrets only in the `env:` of the step that uses them; read-only `GITHUB_TOKEN` by default |
| Shell / output injection from inputs or binaries | Inputs passed through `env:`; rollout validated; multi-line outputs refused; versions restricted to a safe character set |
| Supply chain (actions, gems) | Actions pinned to commit SHAs; gems locked with checksums and installed frozen; jobs holding store keys don't restore the shared gem cache; Dependabot proposes updates after a 7-day cooldown |
| Binaries published by accident | The archive job refuses to run in a public repository, and the verified binaries are only kept as workflow artifacts in a private one |
| Secrets committed by accident | `.gitignore` blocks key files and binaries; the setup script reads secrets from stdin |

## What the pipeline does to protect tokens

All of these are in the code today:

- **Minimal permissions.** Every workflow sets `permissions: contents: read`. Only the `archive` job of `release.yml` gets `contents: write`, to create the GitHub Release. It runs in the `store-testing` environment, so that environment's deployment-branch rules also limit which branches can create release tags, and it refuses to run in a public repository.
- **No persisted Git credentials.** Every `actions/checkout` uses `persist-credentials: false`.
- **Step-scoped secrets.** `LIFETIME_API_TOKEN` is only given to the fetch / status step, and the store keys only to the fastlane step. Nothing secret is set at workflow level (`LIFETIME_HOST` is a non-secret variable).
- **Pinned dependencies.** Third-party actions are pinned to full commit SHAs. `Gemfile.lock` contains gem checksums and CI installs with `BUNDLE_FROZEN=true`. Jobs that hold store keys run `bundle install` without restoring the gem cache that **Validate** (which has no secrets) saves.
- **LifeTime token handling** (`scripts/lifetime.py`, covered by `tests/test_lifetime.py`):
  - attached with `add_unredirected_header`, so it is never forwarded on a redirect;
  - sent only when the download host is the LifeTime host, never to a storage or CDN host;
  - non-HTTPS download URLs and redirects to non-HTTPS URLs are refused.
- **Output hygiene.** Error and warning annotations are escaped to a single line, so text from a LifeTime response or a binary can't start another workflow command. Values written to `$GITHUB_OUTPUT` cannot contain newlines, and version / build strings read from the binary must match `^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$`.
- **Input validation.** `android_rollout` must be a number greater than 0 and at most 100 (checked in the `plan` job and again in the Fastfile, which also checks the release status and the iOS target). All other inputs are choices or booleans, except `environment` and `release_notes`, which are free text and only reach the scripts through `env:` (an `environment` that isn't a LifeTime environment name simply fails the lookup).
- **Setup script.** `scripts/setup-secrets.sh` passes every secret to `gh` on stdin, never as a command-line argument, and reads the LifeTime token without echo. The App Store Connect Key ID and Issuer ID are shown as you type them: they are identifiers, not keys (the `.p8` is the secret). Only the non-secret variables (`LIFETIME_HOST`, `RELEASE_NOTES_LANGUAGES`) are passed as arguments.
- **Integrity record.** Each GitHub Release lists the SHA-256 of the archived binaries.

## Secret handling rules

- Store credentials and tokens live only in GitHub Actions secrets and in your team's secret manager ([docs/SETUP.md](docs/SETUP.md)). Never in the repository, an issue, a PR, a chat or a local file you keep.
- Delete local copies of `.json` and `.p8` key files after running `scripts/setup-secrets.sh`.
- Never add a step that prints, echoes, uploads or caches a secret, or that writes one to a file that could become an artifact.
- Never pass a secret as a command-line argument (it can show up in process lists and logs).
- Use the dedicated machine identities described in [docs/SETUP.md](docs/SETUP.md), never personal accounts.
- AI assistants follow the guardrails in [AGENTS.md](AGENTS.md).

## Known gaps

These are open today. Maintainers: the fixes are in [docs/SETUP.md](docs/SETUP.md).

1. **The approval gate may not be enforced.** In a private repository, **required reviewers** on an environment need GitHub Enterprise Cloud (plan × visibility table in [docs/SETUP.md §4](docs/SETUP.md#4-github-environments-and-your-plan)), and an environment created automatically by a workflow has no rules at all. Until `store-production` is confirmed to require reviewers, assume Google Play `production` and App Store review run **without approval**.
2. **Secrets at repository level.** `scripts/setup-secrets.sh` stores secrets at repository level unless `--env` is given. Repository-level secrets can be read by any workflow on any branch pushed by someone with write access, so the environment gate doesn't protect the credentials themselves. Moving them to environment secrets also requires adding `environment:` to the **Check connections** jobs, which use the same secrets.
3. **`alpha` and `beta` are not gated.** Only Android `production` and iOS `app-store-review` use `store-production`. Google Play `alpha` / `beta` (open testing can be public) and TestFlight run in `store-testing`.
4. **Anyone with write access can release.** Running a `workflow_dispatch` workflow only requires write access to the repository.
5. **CODEOWNERS is advisory** unless branch protection on `main` requires code owner review.
6. **Archived binaries** in GitHub Releases are readable by everyone with read access to the repository ([docs/VERSION_ARCHIVING.md](docs/VERSION_ARCHIVING.md)).
7. **A production release can take its build from any LifeTime environment.** Nothing ties `store-production` to your production environment: a build from e.g. a testing environment with the store identifier and an App Store profile would be accepted.
