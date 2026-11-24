# Guide for AI coding assistants

This repository publishes one OutSystems mobile app, the one in `config/app.json` (MABS native builds from OutSystems LifeTime) to **Google Play** and **App Store Connect** with fastlane in GitHub Actions. It releases **only this app**: there is no app selector, and adding other apps is out of scope (`tests/test_lifetime.py` asserts that `--app` is rejected).

## Guardrails (read first)

- **Never** run `gh workflow run`, re-run a run, or dispatch any workflow without explicit, per-run human approval. `release.yml` reaches real customers, and store submissions cannot be undone. A `dry_run` still downloads from LifeTime and (Android) calls Google Play.
- `check-credentials.yml` is read-only but uses production credentials. Ask before running it.
- Never call the LifeTime, Google Play or App Store Connect APIs from your session (that includes `lifetime.py status` and `fetch`). Everything is covered by offline unit tests.
- Never print, log, commit or write secrets to disk. `.gitignore` blocks `*.p8`, `*.json.key`, `*.aab`, `*.apk`, `*.ipa` and `build/`; keep it that way.
- Pin new GitHub Actions to a full commit SHA with a `# vX.Y.Z` comment, and keep `persist-credentials: false` on every checkout.
- Pass workflow inputs to shell steps through `env:`, never by interpolating `${{ inputs.* }}` inside `run:`.
- Pass secrets only to the step that needs them (step-level `env:`), and keep top-level `permissions: contents: read`.
- After changing the `Gemfile`, run `bundle lock --add-checksums` and commit `Gemfile.lock`. CI installs with `BUNDLE_FROZEN=true` and fails on a stale lockfile.
- Keep `scripts/lifetime.py` standard-library only (it runs on bare runners, without pip).

## File map

| Path | Purpose |
|---|---|
| `config/app.json` | The app: `id`, `name`, `lifetimeAppId`, `androidPackage`, `iosBundleId`, default `environment`, `platforms` |
| `scripts/lifetime.py` | CLI: `plan`, `fetch`, `status`, `inspect`, `check-config` |
| `fastlane/Fastfile` | Lanes `android upload`, `android check_access`, `ios upload`, `ios check_access` |
| `.github/workflows/release.yml` | "Release to stores", manual: `plan` → `android` ∥ `ios` → `archive` |
| `.github/workflows/check-credentials.yml` | "Check connections", manual, read-only: LifeTime status, Google Play, App Store Connect |
| `.github/workflows/validate.yml` | "Validate", PR / push to `main`: `check-config`, unit tests, actionlint, `fastlane lanes`, Fastfile decision tests |
| `scripts/setup-secrets.sh` | Sets the Actions variables and secrets through `gh` (repository level, or `--env <name>`) |
| `tests/test_lifetime.py` | Unit tests; LifeTime is replaced by `FakeLifeTime`, no network |
| `tests/test_fastfile.rb` | Fastfile decision tests: evaluates the Fastfile against stand-ins for the fastlane actions and Spaceship and checks what would be sent to the stores; plain `ruby`, no gems, no network |
| `Gemfile`, `Gemfile.lock` | `fastlane ~> 2.225`, locked with checksums (Bundler 2.6.9) |
| `docs/` | `RELEASING_GUIDE.md`, `TROUBLESHOOTING.md`, `VERSION_ARCHIVING.md`, `SETUP.md`, `ARCHITECTURE.md` |
| `CONTRIBUTING.md`, `SECURITY.md` | Development workflow; security model and known gaps |

## Contracts

### `config/app.json`

`check_config()` requires: `id` matching `[a-z0-9][a-z0-9-]*`, a non-empty `name`, a UUID `lifetimeAppId` (the LifeTime application key), a non-empty `environment`, `platforms` a non-empty subset of `["android", "ios"]`, and a reverse-DNS `androidPackage` / `iosBundleId` for each listed platform (Android: letters, digits and underscores, each segment starting with a letter; iOS: letters, digits and hyphens). Values of the wrong type are reported as errors, and a missing or non-JSON file raises `PipelineError`. Every command except `inspect` loads and validates it. `fetch` fails if the binary's identifier differs from it **or cannot be read**.

`fetch` and `status` refuse to call LifeTime while `lifetimeAppId` is the template placeholder `00000000-0000-0000-0000-000000000000` (`require_configured()`); `plan` and `check-config` accept it, so CI passes on the unconfigured template.

### `lifetime.py` CLI

| Command | Arguments | Network | `$GITHUB_OUTPUT` keys |
|---|---|---|---|
| `plan` | `--platform both\|android\|ios` (default `both`), `--environment` (empty or `default` → config) | no | `id`, `name`, `environment`, `android`, `ios` (`true`/`false`), `android_package`, `ios_bundle_id` |
| `fetch` | `--platform android\|ios` (required), `--environment`, `--out` (default `build`), `--dry-run` | LifeTime | `file` (absolute path), `identifier`, `version`, `build`, `environment`. With `--dry-run`: only `file` (empty), `version`, `environment` |
| `status` | none | LifeTime | none (Markdown table to stdout and step summary) |
| `inspect` | `<file>` (`.aab`, `.apk`, `.ipa`) | no | none (JSON to stdout) |
| `check-config` | none | no | none |

- `fetch` and `status` need `LIFETIME_HOST` and `LIFETIME_API_TOKEN`.
- Errors raise `PipelineError`, are printed as `::error::` annotations when `GITHUB_ACTIONS` is set (escaped to a single line), and exit with code 1.
- `set_outputs` refuses values containing newlines. `version` and `build` must match `^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$`.
- `fetch` names the file `<id>-<version>-<build>.<ext>`, using the version inside the binary, not LifeTime's `VersionNumber` (which can lag).
- `inspect` output for an IPA includes `profileType` (`app-store`, `ad-hoc`, `development`, `enterprise`, `unsigned`), `profileExpires` and `profileExpired`.
- A LifeTime response that isn't JSON, a timeout or dropped connection, and a malformed AAB / IPA, raise `PipelineError` with a readable message instead of a traceback.
- `LIFETIME_HOST` may be a bare host or a URL; only `host[:port]` is kept and HTTPS is always used. Anything that isn't a host name is refused.

### Fastfile environment

`ENV.fetch` without a default raises when a variable is missing.

| Lane | Required | Optional (default) |
|---|---|---|
| `android upload` | `RELEASE_FILE`, `ANDROID_PACKAGE`, `GOOGLE_PLAY_JSON_KEY` | `ANDROID_TRACK` (`internal`), `ANDROID_RELEASE_STATUS` (`completed`), `ANDROID_ROLLOUT` (`100`), `ANDROID_VERSION_CODE`, `RELEASE_NOTES`, `RELEASE_NOTES_LANGUAGES` (`en-US`), `DRY_RUN` (`false`) |
| `ios upload` | `RELEASE_FILE`, `IOS_BUNDLE_ID`, `IOS_VERSION`, `IOS_BUILD`, `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8` | `IOS_TARGET` (`testflight`), `RELEASE_NOTES`, `IOS_USES_NON_EXEMPT_ENCRYPTION` (`false`), `DRY_RUN` (`false`) |
| `android check_access` | `ANDROID_PACKAGE`, `GOOGLE_PLAY_JSON_KEY` | |
| `ios check_access` | `IOS_BUNDLE_ID`, `ASC_KEY_ID`, `ASC_ISSUER_ID`, `ASC_KEY_P8` | |

`DRY_RUN` and `IOS_USES_NON_EXEMPT_ENCRYPTION` are true for `true`, `1`, `yes` or `y`. `RELEASE_NOTES_LANGUAGES` applies to Google Play only; App Store "What's New" goes to the languages the App Store version already has. `android upload` also checks `ANDROID_RELEASE_STATUS` (`completed` or `draft`) and `ANDROID_ROLLOUT` (`> 0` and `<= 100`), and ignores the rollout on `internal` and for `draft` releases. `ios upload` checks `IOS_TARGET` (`testflight` or `app-store-review`). Secrets and variables are described in [docs/SETUP.md](docs/SETUP.md).

### `release.yml` behaviour worth knowing

- Job environment: `store-production` for Android `production` and iOS `app-store-review` (both only when not a dry run); `store-testing` for everything else, including `alpha` and `beta`.
- `concurrency: release-to-stores`, no cancel-in-progress. GitHub keeps only one pending run per group.
- The `environment` input is free text (default `default` = `environment` in `config/app.json`); any other value must be an exact LifeTime environment name. It reaches the scripts only through `env:`.
- `plan` validates `android_rollout` with a bash regex and `awk` (no `bc`, which isn't guaranteed on runners).
- Binaries go to a workflow artifact `<id>-android` / `<id>-ios` (30 days; only in a private repository, since artifacts of a public one are downloadable by any signed-in user), then `archive` creates the release `<id>-v<version>-<YYYYMMDDHHMM>` with `--latest=false`. `archive` is the only job with `contents: write`; it runs in `store-testing` and refuses to run in a public repository.
- Jobs that hold store keys run `bundle install` without the shared gem cache; only `validate.yml` (no secrets) uses `bundler-cache`. Dependabot (`.github/dependabot.yml`) proposes action and gem updates weekly.

## Verify a change

```bash
python3 scripts/lifetime.py check-config
python3 -m unittest discover -s tests -v
ruby tests/test_fastfile.rb                    # Fastfile decisions, offline, no gems needed
bundle install && bundle exec fastlane lanes   # proves the Fastfile loads with the locked fastlane
actionlint                                     # if installed; CI runs it in validate.yml
```

Full workflow in [CONTRIBUTING.md](CONTRIBUTING.md).

## Known quirks

1. **HTTP 204 from `/content`**: LifeTime only serves binaries of **tagged** versions. Untagged hotfixes or Service Center builds return 204, and `fetch` explains this.
2. **Inconsistent JSON casing** (`nativePlatform` vs `NativePlatform`, `url` vs `Url`): always read LifeTime fields with `ci()`.
3. **Token scope**: the LifeTime token is attached with `add_unredirected_header`, only for the LifeTime host (compared in lower case), and only over HTTPS; redirects to non-HTTPS URLs are refused. Tests cover this. Keep it that way.
4. **AAB vs APK**: `fetch` asks for `aab`, then `apk`, and detects the real type from the archive contents. APK identifiers can only be read with `aapt2`; without it, `fetch` fails.
5. **iOS signing**: `fetch` reads `embedded.mobileprovision`; anything other than an App Store profile fails before Apple is contacted. `get-task-allow: true` always means development (even with no device list), and an expired profile fails too.
6. **Version code reuse on Google Play**: when Play says the version code was already used, the Fastfile looks up (read-only) which of `internal`, `alpha`, `beta`, `production` holds it. On the requested track already: it sets that release's rollout (fails if it is already fully released, or for `draft`). On another track: it promotes it from there (first match in that order) with the requested status, rollout and release notes. On no track: it fails with Play Console instructions. Like Play Console, the new release replaces the one on the target track.
7. **App Store Connect duplicate**: `ios upload` first asks the `buildUploads` API for this exact version and build, filtered server side on state `PROCESSING,COMPLETE` (in responses `state` is an object, so don't compare it to a string). If there is one it doesn't upload again; with release notes or `app-store-review` it waits for processing, sets "What to Test" (with notes, via `upload_to_testflight` `distribute_only`, testers unchanged), then submits if requested. An earlier `FAILED` upload is retried; if the lookup itself fails, it uploads.
8. **iOS dry run** makes no App Store Connect call (it doesn't even create the API key); Android dry run calls Google Play with `validate_only`. In `release.yml`, `fetch` runs without `--dry-run`, so a dry run still downloads and verifies both binaries.
9. **Spaceship authentication**: `app_store_connect_api_key` sets the shared `Spaceship::ConnectAPI` token; lanes call `Spaceship::ConnectAPI::App.find(bundle_id)` without a `client:`. `Spaceship::ConnectAPI::Client.new` has no `key:` argument.
10. **Manual release**: `deliver` only applies `automatic_release: false` while it updates the version's metadata, so `ios upload` always passes `skip_metadata: false`, with or without release notes. Don't make it conditional again.
11. **App Store "What's New"** goes only to the languages the version already has (edit version, else live, else the app's primary language): listing a missing language would make deliver add an empty localization and fail the submission.
12. **`check-credentials.yml`** has its own `plan` job; `android` / `ios` are skipped when the platform isn't in `platforms`. It runs `plan` with the runner's preinstalled `python3` (no `setup-python`), so don't rely on features newer than the minimum in [CONTRIBUTING.md](CONTRIBUTING.md#prerequisites) (Python 3.9).
