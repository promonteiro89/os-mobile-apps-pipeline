# Setup and maintenance

How to set the pipeline up for your app, and how to keep its credentials safe. "Maintainer" on this page means whoever has **admin rights** on the repository: in a one-person setup, that's you. People who only release don't need this page: see the [README](../README.md) and [docs/RELEASING_GUIDE.md](RELEASING_GUIDE.md). How the pipeline works internally is in [docs/ARCHITECTURE.md](ARCHITECTURE.md); reporting vulnerabilities and the list of known gaps are in [SECURITY.md](../SECURITY.md).

> [!WARNING]
> Every credential on this page can publish the real app to Google Play and App Store Connect. Treat them as production secrets: keep them only in GitHub secrets and your team's secret manager (e.g. a password-manager item), never in a file in the repository, a chat or a ticket.

## First-time setup

Do these in order. Each step links to the detail further down.

1. **Create your repository** from this template: **Use this template → Create a new repository → Private**. Don't fork it: a fork of a public repository is public, and the archive step would then publish your app binaries.
2. **LifeTime service account token** ([§6 → LifeTime](#lifetime-service-account)). You need it first, to look up your app's key.
3. **Find your app's `lifetimeAppId`**, the LifeTime application key:

   ```bash
   curl -s -H "Authorization: Bearer $LIFETIME_API_TOKEN" \
     "https://mycompany-lt.outsystemsenterprise.com/lifetimeapi/rest/v2/applications/"
   ```

   Take the `Key` of your app from the response (its `Kind` must be `Mobile`).
4. **Edit [`config/app.json`](../config/app.json)**: `id`, `name`, `lifetimeAppId`, `androidPackage`, `iosBundleId`, the default LifeTime `environment` (exact name) and `platforms`. Check with `python3 scripts/lifetime.py check-config`. `fetch` and `status` refuse to run while `lifetimeAppId` is still the template placeholder.
5. **Set up `.github/CODEOWNERS`**: it ships commented out with `@your-org/release-owners` placeholders. Uncomment it with your own user or team, or delete it ([§5, step 3](#3-keep-codeowners-current)).
6. **Check the store prerequisites** ([below](#store-prerequisites)).
7. **Google Play service account** ([§6 → Google Play](#google-play-service-account)).
8. **App Store Connect API key** ([§6 → App Store Connect](#app-store-connect-team-api-key)).
9. **Secrets and variables**: `./scripts/setup-secrets.sh` ([§2](#2-setting-secrets-and-variables)). Set `IOS_USES_NON_EXEMPT_ENCRYPTION` yourself if it applies ([§1](#1-secrets-and-variables)).
10. **Environments** `store-production` and `store-testing` ([§4](#4-github-environments-and-your-plan) and [§5, step 4](#4-configure-the-store-production-environment)).
11. **Check connections** ([§3](#3-verify-without-publishing)).
12. **A dry run** of **Release to stores** ([RELEASING_GUIDE.md](RELEASING_GUIDE.md)), then a first real release to `internal` / TestFlight.

### Store prerequisites

The pipeline publishes new builds of an app that already exists in both stores. Before the first run:

- **Google Play**: the app exists in Play Console and its **first AAB was uploaded by hand** (Google Play doesn't accept API uploads before that). The AAB that MABS produces must be signed with the **upload key** registered under Play App Signing.
- **App Store Connect**: the app record exists, and the first version's metadata (screenshots, privacy details, age rating) was filled in by hand. The pipeline doesn't manage store listings.
- **iOS export compliance**: declare `ITSAppUsesNonExemptEncryption` in the app's `Info.plist` (through MABS extensibility). Otherwise TestFlight builds wait at "Missing Compliance" and review submissions need an answer: the pipeline then uses the `IOS_USES_NON_EXEMPT_ENCRYPTION` variable (default `false`). Set it to `true` if your app uses non-exempt encryption.
- **Release notes locales**: `RELEASE_NOTES_LANGUAGES` (default `en-US`) must be languages of your **Google Play** store listing. App Store "What's New" goes to the languages your App Store version already has.

Contents:

1. [Secrets and variables](#1-secrets-and-variables)
2. [Setting secrets and variables](#2-setting-secrets-and-variables)
3. [Verify without publishing](#3-verify-without-publishing)
4. [GitHub environments and your plan](#4-github-environments-and-your-plan)
5. [Hardening checklist](#5-hardening-checklist)
6. [Store credentials](#6-store-credentials)
7. [LifeTime prerequisites](#7-lifetime-prerequisites)
8. [Key rotation runbooks](#8-key-rotation-runbooks)
9. [Onboarding and offboarding maintainers](#9-onboarding-and-offboarding-maintainers)

## 1. Secrets and variables

By default (the setup script without `--env`) all of these are stored at **repository level** in **Settings → Secrets and variables → Actions**. Environment-scoped secrets are safer: see [§5](#5-hardening-checklist).

| Name | Kind | Consumed by (workflow → job → step) | Content |
|---|---|---|---|
| `LIFETIME_HOST` | Variable | `release.yml` → workflow-level `env` (used by `android` / `ios` → *Fetch binary from LifeTime*); `check-credentials.yml` → `lifetime` → *Show native build status in every environment* | `mycompany-lt.outsystemsenterprise.com` (a bare host or `https://` URL; the script normalises it) |
| `RELEASE_NOTES_LANGUAGES` | Variable (optional) | `release.yml` → `android` → *Upload to Google Play* | Comma-separated Google Play locales for the release notes. Defaults to `en-US` when unset. App Store "What's New" uses the App Store version's own languages instead |
| `IOS_USES_NON_EXEMPT_ENCRYPTION` | Variable (optional) | `release.yml` → `ios` → *Upload to App Store Connect* | Export-compliance answer (`true` / `false`, default `false`) for builds whose `Info.plist` doesn't declare `ITSAppUsesNonExemptEncryption`. Passed to TestFlight and to the review submission. Prefer declaring the key in `Info.plist` through MABS extensibility. Not set by the setup script |
| `LIFETIME_API_TOKEN` | Secret | `release.yml` → `android` / `ios` → *Fetch binary from LifeTime*; `check-credentials.yml` → `lifetime` → *Show native build status…* | Bearer token of the LifeTime service account (REST API v2) |
| `GOOGLE_PLAY_JSON_KEY` | Secret | `release.yml` → `android` → *Upload to Google Play*; `check-credentials.yml` → `android` → *Check Google Play access* | Full JSON key file of the Google Cloud service account (the file content, not a path) |
| `ASC_KEY_ID` | Secret | `release.yml` → `ios` → *Upload to App Store Connect*; `check-credentials.yml` → `ios` → *Check App Store Connect access* | App Store Connect API key ID |
| `ASC_ISSUER_ID` | Secret | same as `ASC_KEY_ID` | App Store Connect issuer ID |
| `ASC_KEY_P8` | Secret | same as `ASC_KEY_ID` | Content of `AuthKey_<KEY_ID>.p8`, including the `BEGIN/END PRIVATE KEY` lines (plain text, not base64: the Fastfile sets `is_key_content_base64: false`) |
| `GITHUB_TOKEN` | Automatic | `release.yml` → `archive` → *Create GitHub Release* (`contents: write` on that job only) | Provided by GitHub; nothing to configure |

Notes:

- Secrets are passed as `env` **only on the step that needs them**, never at workflow or job level. The `plan` job, artifact uploads and `bundle install` never see a store key.
- The verified binaries are kept as workflow artifacts only in a **private** repository: artifacts of a public repository can be downloaded by any signed-in GitHub user.
- Jobs that hold store keys don't restore the shared gem cache: they run `bundle install` from `Gemfile.lock` (`BUNDLE_FROZEN=true`, checksums verified). Only **Validate**, which has no secrets, uses the cache.
- Nothing else is read from the environment. App identity (LifeTime key, package / bundle ID, default LifeTime environment) lives in [`config/app.json`](../config/app.json), not in secrets.
- If an environment secret and a repository secret have the same name, the job running in that environment gets the environment value.

### Secret manager

Keep one item in your team's secret manager (e.g. a password-manager item) with the LifeTime token, the Google Play JSON key file, the `.p8` file and the key / issuer IDs. Reference only (not read by the pipeline): your Google Cloud project, service account `store-release@<your-gcp-project>.iam.gserviceaccount.com`. The secret manager is the source of truth: GitHub secrets are write-only, so they cannot be read back.

## 2. Setting secrets and variables

You need admin rights on the repository. Do it from your own machine, never from CI.

### With the setup script

[`scripts/setup-secrets.sh`](../scripts/setup-secrets.sh) prompts for the two variables (your LifeTime host, and the Google Play release-notes locales), then for each secret. Press Enter to skip any value that is already set: nothing is overwritten with an empty or default value. Release notes use `en-US` while `RELEASE_NOTES_LANGUAGES` has never been set. It doesn't set `IOS_USES_NON_EXEMPT_ENCRYPTION`: set that with `gh variable set` if you need it.

```bash
./scripts/setup-secrets.sh                                    # current repository, repository-level secrets
./scripts/setup-secrets.sh --repo <owner>/<repo>              # another repository
./scripts/setup-secrets.sh --env store-production             # secrets scoped to a GitHub environment
./scripts/setup-secrets.sh --repo <owner>/<repo> --env store-testing
```

- `--env` scopes **secrets only**. The two variables are always set at repository level.
- `--env` does not create the environment's rules. Configure them first (see [§5, step 4](#4-configure-the-store-production-environment)).
- The LifeTime token is read with `read -s` (no echo). The Key ID and Issuer ID prompts **do echo**, which is acceptable because they are identifiers, not keys. The JSON key and `.p8` are read from file paths.
- Every secret reaches `gh` on stdin, never as a command-line argument, so it doesn't show up in `ps` or shell history. The two variables are not secret and are passed with `--body`.
- At the end it prints a `gh secret list` command to check what was set. Then delete the key files from your disk.

### With the GitHub CLI

Always pass values on stdin, never with `--body`:

```bash
gh secret set GOOGLE_PLAY_JSON_KEY --repo <owner>/<repo> < play-key.json
gh secret set ASC_KEY_P8          --repo <owner>/<repo> < AuthKey_XXXXXXXXXX.p8
gh secret set LIFETIME_API_TOKEN  --repo <owner>/<repo>            # prompts for the value
gh secret set ASC_KEY_ID          --repo <owner>/<repo> --env store-production   # environment-scoped

gh variable set LIFETIME_HOST --repo <owner>/<repo> --body "mycompany-lt.outsystemsenterprise.com"
gh variable set IOS_USES_NON_EXEMPT_ENCRYPTION --repo <owner>/<repo> --body "false"   # optional
gh secret list --repo <owner>/<repo> [--env <environment>]
```

### With the web UI

- Repository secrets / variables: **Settings → Secrets and variables → Actions** → *Secrets* or *Variables* tab → **New repository secret / variable**.
- Environment secrets: **Settings → Environments → `<environment>` → Environment secrets → Add environment secret**.

Paste the whole JSON key or `.p8` content into the value box. Avoid trailing spaces in the IDs.

## 3. Verify without publishing

The **Check connections** workflow ([`check-credentials.yml`](../.github/workflows/check-credentials.yml)) is read-only: it uploads nothing and changes nothing in any store. It still uses the production credentials, so run it deliberately (Actions → **Check connections** → **Run workflow**, or `gh workflow run check-credentials.yml`).

| Job | What it does | Proves |
|---|---|---|
| `plan` (*Read app config*) | `lifetime.py plan`: reads `config/app.json` (no network, no secrets) | Which platforms the app has. The `android` / `ios` jobs are skipped for a platform that isn't in `platforms` |
| `lifetime` (*LifeTime build status*) | `lifetime.py status`: lists every LifeTime environment and the app's version and native build flags (configured / binary / config changed) | `LIFETIME_HOST` + `LIFETIME_API_TOKEN` work and the service account can see the app |
| `android` (*Google Play access*) | `fastlane android check_access`: reads the version codes on the `production` track | `GOOGLE_PLAY_JSON_KEY` works and the service account has access to `com.example.myapp` |
| `ios` (*App Store Connect access*) | `fastlane ios check_access`: creates the API token from the three `ASC_*` secrets and finds the app by bundle ID | The three `ASC_*` secrets form a valid key and the key's team owns the app |

It proves **access**, not **publish permission**: a Play service account without release permissions, or an App Store Connect key with a role too weak to submit, passes this check. A `dry_run` release (Android validates the edit in Google Play without committing it) is the closest test of publish permission. iOS dry runs make no App Store Connect call at all.

## 4. GitHub environments and your plan

[`release.yml`](../.github/workflows/release.yml) runs each platform job in a GitHub environment, chosen from the inputs:

| Job and inputs | Environment |
|---|---|
| `android` with `android_track=production` and not `dry_run` | `store-production` |
| `ios` with `ios_target=app-store-review` and not `dry_run` | `store-production` |
| Everything else: `internal`, `alpha`, `beta`, TestFlight, every dry run | `store-testing` |
| `archive` | `store-testing` (no store secrets; its deployment-branch rules limit which branches can create release tags) |
| `plan`, and all of `check-credentials.yml` and `validate.yml` | none |

Facts about GitHub environments that matter here:

- A workflow that references an environment that doesn't exist **creates it with no protection rules and no secrets**. So `store-production` gives no protection until an admin configures it.
- Environment secrets are only available to jobs that use that environment, and only after its protection rules pass.
- What you get depends on the repository's visibility and the owner's plan. At the time of writing, per GitHub's documentation (check [GitHub's current docs](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment) before relying on it):

| Repository | Environments, environment secrets, deployment branches | Required reviewers, wait timers | Branch protection / rulesets |
|---|---|---|---|
| Public, any plan (Free included) | Yes | Yes | Yes |
| Private, personal account on Free | No | No | No |
| Private, personal account on Pro | Yes | No | Yes |
| Private, organisation on Free | No | No | No |
| Private, organisation on Team | Yes | No | Yes |
| Private, organisation on Enterprise Cloud | Yes | Yes | Yes |

- Keep the repository **private** (it holds store credentials and the archived binaries; the archive step refuses to run in a public repository). Then `store-production` can only require an approval on GitHub **Enterprise Cloud**. On Pro or Team you still get environment secrets, deployment branches and branch protection.

The gaps this can leave (repository-level secrets, an approval that isn't enforced, ungated `alpha` / `beta`, production builds from any LifeTime environment) are listed in [SECURITY.md → Known gaps](../SECURITY.md#known-gaps). The checklist below closes them.

## 5. Hardening checklist

Work through these in order. Steps 1 to 6 are settings only. Steps 7 to 9 need workflow changes reviewed through a pull request.

### 1. Confirm your plan and access

- [ ] Find out which plan owns the repository (personal Pro, organisation Team or Enterprise Cloud): required reviewers in step 4 depend on it ([§4](#4-github-environments-and-your-plan)).
- [ ] Check **Settings → Secrets and variables → Actions** and **Settings → Environments**. If anything is missing, create it with `./scripts/setup-secrets.sh --repo <owner>/<repo>`.
- [ ] In an organisation, give access through **teams**, not individual collaborators: for example `mobile-release-maintainers` (Admin) and `mobile-releasers` (Write). Anyone with **Write** can dispatch **Release to stores**. Give Write only to people allowed to release.

### 2. Protect the release branch

The organisation's rulesets may already cover this (check **Settings → Rules → Rulesets**). Otherwise, for the branch releases run from:

- [ ] Require a pull request before merging, at least **1 approval**, dismiss stale approvals on new commits.
- [ ] Require review from **Code Owners**.
- [ ] Require status checks to pass: the `validate` job of **Validate**.
- [ ] Block force pushes and deletions. Don't allow bypass, or keep the bypass list to the organisation admins.

### 3. Keep CODEOWNERS current

`.github/CODEOWNERS` ships commented out, with `@your-org/release-owners` placeholders for the release path (`/.github/`, `/fastlane/`, `/scripts/`, `/config/`, `/Gemfile*`). It only blocks merges when step 2 requires code-owner review.

- [ ] Uncomment it with your own user or team (for example `@<org>/mobile-release-maintainers`), or delete the file. A team must have write access to the repository for CODEOWNERS to apply.
- [ ] With a single code owner, that person can't get a code-owner review of their own pull requests. Have at least two maintainers.

### 4. Configure the `store-production` environment

**Settings → Environments → store-production**:

- [ ] **Deployment branches and tags** → *Selected branches and tags* → add `main` (or *Protected branches only*). Runs dispatched from any other branch then cannot use `store-production` or its secrets.
- [ ] **Required reviewers** (for a private repository, needs Enterprise Cloud: see [§4](#4-github-environments-and-your-plan)): at least two people, so someone other than the releaser is always available. Only one approval is needed per run.
- [ ] **Prevent self-review**, so whoever dispatched the run can't approve it.
- [ ] Leave **Allow administrators to bypass configured protection rules** unticked, if your plan shows the option.
- [ ] Configure `store-testing` the same way for deployment branches (`main` only). Then even testing-track uploads, and the release tags the `archive` job creates, only use reviewed workflow code. Trade-off: dry runs from feature branches stop working.

If required reviewers aren't available on your plan, the remaining controls are: deployment branches = `main`, a protected `main`, Write access only for releasers, environment-scoped secrets, plus the store-side controls. App Store submissions already use manual release (`automatic_release: false`). On Google Play you can enable **Managed publishing**, so approved changes only go live when someone publishes them in Play Console.

### 5. Move the secrets into environments

Goal: no store or LifeTime secret at repository level.

- [ ] Configure the environments first (step 4), so the secrets are protected from the moment they exist.
- [ ] Set the production credentials in `store-production`: `./scripts/setup-secrets.sh --env store-production`.
- [ ] Set credentials in `store-testing` too. The testing jobs run there and would otherwise get empty values. Use the separate testing Play service account (step 6) here.
- [ ] Delete the repository-level copies: **Settings → Secrets and variables → Actions → Repository secrets**. `LIFETIME_HOST`, `RELEASE_NOTES_LANGUAGES` and `IOS_USES_NON_EXEMPT_ENCRYPTION` can stay repository variables; they're not sensitive.

What changes in the workflows (pull request needed):

- `release.yml`: nothing for the `android` / `ios` jobs, which already declare `environment:`.
- `check-credentials.yml`: its three jobs that use secrets (`lifetime`, `android`, `ios`) declare no environment, so after the move they would get empty secrets and fail. Add `environment: store-testing` to each job. Using `store-production` would also require an approval for every check, and the check would only validate the production credentials.
- Running **Check connections** in `store-testing` proves only the testing credentials. Dry runs also run in `store-testing`, so they don't test the production credentials either. To check the production set without publishing, add a second copy of the `android` / `ios` check jobs with `environment: store-production` (it then goes through the environment's rules, approval included).

### 6. Use a separate Google Play service account for testing

- [ ] Create a second service account in your Google Cloud project (for example `store-testing`) with its own JSON key. See [§6](#6-store-credentials).
- [ ] In Play Console give it, on your app only: **Release apps to testing tracks** and **Manage testing tracks and edit tester lists**. Do **not** give it **Release to production**.
- [ ] Store its key as `GOOGLE_PLAY_JSON_KEY` in `store-testing`, and the production account's key in `store-production`.
- [ ] Run **Check connections** after step 5. The `android` job reads the `production` track's version codes. If the testing account can't read them, also give it view-only app access in Play Console.
- [ ] App Store Connect has no per-app API keys, and the same key both uploads to TestFlight and submits for review. A separate testing key reduces blast radius (it can be revoked on its own) but not privilege. It's still recommended for `store-testing`.
- [ ] LifeTime: the same token reads every environment. A separate read-only service account for `store-testing` is optional.

### 7. Gate public testing tracks (workflow change)

Google Play open testing (`beta` here) is public, and closed testing (`alpha`) can reach large tester lists. Route every non-`internal` Android track to `store-production`:

```yaml
# release.yml, job android
environment: ${{ inputs.android_track != 'internal' && !inputs.dry_run && 'store-production' || 'store-testing' }}
```

After this change `store-testing` only uploads to `internal` (and runs dry runs). The testing service account from step 6 then needs nothing beyond internal testing, and `alpha` / `beta` use the production account behind the `store-production` rules.

### 8. Production releases only from production LifeTime (workflow change)

Make the `plan` job refuse a production store target unless the build comes from your production environment (the `environment` in `config/app.json`, `Production` in this example). For example, add to the `plan` step's `env`:

```yaml
TRACK: ${{ inputs.android_track }}
IOS_TARGET: ${{ inputs.ios_target }}
```

and to its script:

```bash
if [[ "$TRACK" == "production" || "$IOS_TARGET" == "app-store-review" ]] \
   && [[ "$ENVIRONMENT" != "default" && "$ENVIRONMENT" != "Production" ]]; then
  echo "::error::Production store releases must take the build from Production (got '$ENVIRONMENT')"; exit 1
fi
```

Keep values in `env` and quote them in the script, as the workflow already does. Never interpolate `${{ inputs.* }}` into `run:`.

### 9. Actions policy and supply chain

**Settings → Actions → General**:

- [ ] **Actions permissions**: *Allow `<owner>`, and select non-`<owner>`, actions and reusable workflows*, with exactly the actions the workflows use:
  `actions/checkout@*, actions/setup-python@*, actions/upload-artifact@*, actions/download-artifact@*, ruby/setup-ruby@*, raven-actions/actionlint@*`
- [ ] If your settings offer it, enable the option that requires actions to be pinned to a full-length commit SHA. Every action here is already SHA-pinned.
- [ ] **Workflow permissions**: *Read repository contents and packages permissions* (the workflows already declare `permissions: contents: read`; only `archive` raises it to `contents: write`). Leave *Allow GitHub Actions to create and approve pull requests* unticked.
- [ ] Fork pull request workflows: in a private repository keep *Run workflows from fork pull requests* off. In a public repository, set *Approval for running fork pull request workflows from contributors* to **Require approval for all external contributors**. Fork pull requests run **Validate** only, with a read-only token and no secrets.

**Dependabot** is configured in [`.github/dependabot.yml`](../.github/dependabot.yml): weekly updates for `github-actions` and `bundler`, with a 7-day cooldown before a new version is proposed.

- Dependabot keeps the SHA pins and updates the `# vX.Y.Z` comments.
- `Gemfile.lock` has a `CHECKSUMS` section and CI installs with `BUNDLE_FROZEN=true`. If a Dependabot bundler PR fails `validate` on checksums, regenerate the lock locally with `bundle lock --add-checksums` and push to the PR branch.
- Review every bump to `fastlane` and its dependencies carefully: those gems run with the store keys loaded.
- Also enable **Dependabot alerts** and **secret scanning** (Settings → Code security), where your plan provides them.

## 6. Store credentials

All automation uses dedicated machine identities, never personal accounts.

| Store | Identity | Scope |
|---|---|---|
| Google Play | Service account `store-release@<your-gcp-project>.iam.gserviceaccount.com` (your Google Cloud project) | Per-app permissions on your app only, no account-level permissions |
| App Store Connect | Team API key `store-release`, role **App Manager** | Team-wide: Apple API keys can't be limited to one app, so this key reaches every app of the team |
| LifeTime | Service account API token | Read access to the environments and to your application |

### Google Play (service account)

fastlane's own walkthrough, with screenshots: [supply setup](https://docs.fastlane.tools/actions/supply/#setup).

1. **Google Cloud Console**, your Google Cloud project:
   1. **APIs & Services → Library** → enable **Google Play Android Developer API**.
   2. **IAM & Admin → Service Accounts** → create the service account, e.g. `store-release` (no GCP roles needed for Play publishing).
   3. Service account → **Keys → Add key → Create new key → JSON**. Download it once; it can't be downloaded again.
   4. Consider an organisation policy that restricts service account key creation to the people who rotate keys.
2. **Play Console** → **Users and permissions** → **Invite new users** → the service account email → **App permissions** → add **your app** only, with:
   - Release to production, exclude devices and use Play app signing
   - Release apps to testing tracks
   - Manage testing tracks and edit tester lists
   - Leave **Account permissions** empty.
3. Store the key: `gh secret set GOOGLE_PLAY_JSON_KEY [--env <name>] < key.json`, put the file in your secret manager, delete it locally.
4. Verify with **Check connections** (`android` job), then a `dry_run` on `internal`.

The pipeline never changes store listing metadata, images or screenshots: the Fastfile sets `skip_upload_metadata`, `skip_upload_images` and `skip_upload_screenshots`. It only uploads changelogs when `release_notes` is set. A permission to edit the store listing is not needed.

### App Store Connect (Team API key)

Background: [fastlane and the App Store Connect API](https://docs.fastlane.tools/app-store-connect-api/).

1. **App Store Connect → Users and Access → Integrations → App Store Connect API → Team Keys → (+)**. Name it (e.g. `store-release`), role **App Manager**.
   - App Manager is enough to upload builds, manage TestFlight and submit for review. Don't use Admin.
   - A Team key reaches **every app of the team**, not just this app. Its leak is a team-wide incident; rotate it at the first doubt.
2. Download `AuthKey_<KEY_ID>.p8`. Apple lets you download it **only once**.
3. Note the **Key ID** (on the key row) and the **Issuer ID** (at the top of the Team Keys page).
4. Store `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8` (script or `gh secret set … < AuthKey_<KEY_ID>.p8`), put the file in your secret manager, delete it locally.
5. Verify with **Check connections** (`ios` job).

### LifeTime (service account)

1. LifeTime → **User Management → Service Accounts** → create an account for this pipeline (e.g. `store-release`). Its role needs permission to read your application and download its native builds in every environment you release from (by default the `environment` in `config/app.json`). Role names and permission levels are defined in your LifeTime: check in LifeTime which is the least-privileged role that allows this.
2. Copy the generated token. It's shown only once.
3. Use it to find your app's `lifetimeAppId` ([First-time setup](#first-time-setup), step 3).
4. Store it as `LIFETIME_API_TOKEN`, put it in your secret manager.
5. Verify with **Check connections** (`lifetime` job): every environment should list the app. An environment showing an HTTP error usually means the role has no permission there.

If LifeTime has an IP allowlist, GitHub-hosted runners can't reach it. `lifetime.py` reports `Cannot reach LifeTime at …` and suggests a self-hosted runner or allowlisting the runner IPs.

## 7. LifeTime prerequisites

What must be true in LifeTime before each release (tagged version, finished native builds, Native Platforms set to AAB / App Store signing) is the releaser's [pre-flight checklist](RELEASING_GUIDE.md#2-pre-flight-checklist). Two points matter for maintainers:

- An APK is accepted with a warning, but its identifier can only be read with `aapt2` (looked up on `PATH`, then under `$ANDROID_HOME` / `$ANDROID_SDK_ROOT` `build-tools`); if the runner has none, `fetch` fails. Keep Android on **AAB**.
- The checks `fetch` makes on the LifeTime status response are described in [ARCHITECTURE.md](ARCHITECTURE.md#lifetime-api-calls).

`config/app.json` pins the identity the binary must have: `androidPackage` and `iosBundleId` (`com.example.myapp` in the template: set your own). A binary with a different identifier, or one whose identifier can't be read, is refused.

## 8. Key rotation runbooks

General rule: **create the new credential → update the secret everywhere it lives (each environment) → verify → revoke the old one → update your secret manager**. Never revoke first, except in a suspected leak, where revoking immediately matters more than avoiding a failed run.

Rotate at least yearly, when a maintainer with access leaves (see [§9](#9-onboarding-and-offboarding-maintainers)), and at once if a key may have leaked (a log, a laptop, a fork).

Before rotating, check **Actions** for a running **Release to stores** run. Don't rotate the key it's using mid-run.

### Google Play JSON key

1. Google Cloud Console → **IAM & Admin → Service Accounts** → `store-release` → **Keys → Add key → Create new key → JSON**.
2. `gh secret set GOOGLE_PLAY_JSON_KEY --repo <owner>/<repo> [--env store-production] < new-key.json`. Repeat for each environment holding this account's key.
3. Run **Check connections** and confirm the `android` job passes.
4. Delete the **old** key in Google Cloud (Keys tab, match it by key ID and creation date).
5. Replace the file in your secret manager, delete the local copy.

### App Store Connect API key

1. **Users and Access → Integrations → Team Keys → (+)**, role **App Manager**. Download the new `.p8`.
2. Update all three secrets together: `./scripts/setup-secrets.sh [--env <name>]` (enter the new Key ID, the Issuer ID, the `.p8` path; skip the rest with Enter), or `gh secret set` for `ASC_KEY_ID`, `ASC_ISSUER_ID`, and `ASC_KEY_P8 < AuthKey_<NEW>.p8`. A mismatched ID / `.p8` pair fails authentication.
3. Run **Check connections** and confirm the `ios` job passes.
4. **Revoke** the old key on the Team Keys page. Revocation can't be undone.
5. Replace the file and IDs in your secret manager, delete the local `.p8`.

### LifeTime API token

1. In LifeTime, create a new token for the service account, or a new service account with the same role if you want both valid during the switch.
2. `printf '%s' "$NEW_TOKEN" | gh secret set LIFETIME_API_TOKEN --repo <owner>/<repo> [--env <name>]` (or `gh secret set LIFETIME_API_TOKEN` and paste at the prompt). Repeat for each environment.
3. Run **Check connections** and confirm the `lifetime` job lists every environment.
4. Revoke the old token (or delete the old service account).
5. Update your secret manager.

If LifeTime only lets you regenerate the token in place, the old token stops working at once: do steps 2 and 3 straight away, and avoid running a release in between.

### GitHub variables

`LIFETIME_HOST`, `RELEASE_NOTES_LANGUAGES` and `IOS_USES_NON_EXEMPT_ENCRYPTION` aren't secrets. Change them with `gh variable set` or the UI. `LIFETIME_HOST` changes only if LifeTime moves.

## 9. Onboarding and offboarding maintainers

### Onboarding

1. Give the person access: in an organisation, add them to the maintainers team (Admin), or the releasers team (Write) if they only release; on a personal account, add them as a collaborator.
2. Give them access to the secret-manager item only if they will rotate keys.
3. If required reviewers are enabled, add them to `store-production` reviewers (Settings → Environments).
4. Point them to this page, [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRIBUTING.md](../CONTRIBUTING.md) and [SECURITY.md](../SECURITY.md). Their first change goes through a pull request reviewed by a code owner.
5. They don't need Play Console, App Store Connect or LifeTime accounts to maintain the pipeline. Give personal store access separately, through the store owners, only if their role needs it.

### Offboarding

1. Remove them from the GitHub teams / collaborators and from the `store-production` reviewers.
2. Remove their access to the secret-manager item.
3. **Rotate every credential they could have read** ([§8](#8-key-rotation-runbooks)):
   - Secret-manager access: they had the raw keys. Rotate everything.
   - Write access while secrets are **repository-level**: they could have run a branch workflow that read every secret. Rotate all four credentials (Google Play key, App Store Connect key, LifeTime token, and any testing equivalents).
   - Write access after the move to **environment secrets** with deployment branches = `main` and a protected `main`: rotation is still advisable for `store-testing` if it has no branch restriction.
4. Remove any personal access they had in Play Console, App Store Connect and LifeTime (through the store owners).
5. Check **Settings → Deploy keys**, **Settings → Webhooks**, self-hosted runners (if any) and fine-grained tokens with access to the repository (organisation settings) for anything they created.
6. Note that binaries archived in GitHub Releases stay readable to anyone with read access. Nothing to rotate there, but keep it in mind when granting read access.
