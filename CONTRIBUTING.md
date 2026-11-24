# Contributing

Thanks for helping improve this template. It releases mobile apps to production stores: a bad change can upload the wrong binary, leak a store key, or release to real customers. Keep changes small, test them offline, and never use `release.yml` as a test bench.

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## External contributors

- **Open an issue first** for anything bigger than a typo, so we can agree on the approach before you write code. Use the bug or feature templates.
- **You need no credentials.** Everything is tested offline: see [Local checks](#local-checks). Nobody will ask you to run the release workflow, and you should never run it against a real app to test a change.
- **Never paste** tokens, keys, hostnames, app identifiers or unredacted logs into an issue or pull request.
- Docs use **British English** (behaviour, organisation, licence).
- Security problems go to [SECURITY.md](SECURITY.md), not to a public issue.

The rest of this page applies to everyone; sections marked *Maintainers* are for whoever has admin rights on a copy of the repository.

## Prerequisites

| Tool | Version | Used for |
|---|---|---|
| Python | 3.9 or later (CI uses 3.12) | `scripts/lifetime.py` and its tests. Standard library only, no `pip install` |
| Ruby + Bundler | Ruby 3.3, Bundler 2.6.9 (`BUNDLED WITH` in `Gemfile.lock`) | fastlane |
| [actionlint](https://github.com/rhysd/actionlint) | any recent | Linting workflows (optional locally, required in CI) |
| [GitHub CLI](https://cli.github.com/) (`gh`) | any recent | Opening PRs; maintainers also use it in `scripts/setup-secrets.sh` |

You do **not** need store or LifeTime credentials to contribute.

## Local checks

These mirror the [Validate](.github/workflows/validate.yml) workflow, which runs on every pull request and push to `main`:

```bash
python3 scripts/lifetime.py check-config          # validate config/app.json
python3 -m unittest discover -s tests -v          # unit tests (offline)
ruby tests/test_fastfile.rb                       # Fastfile decision tests (offline, no gems)
actionlint                                        # lint .github/workflows/*.yml
bundle install && bundle exec fastlane lanes      # Fastfile loads with the locked gems
```

`fastlane lanes` only proves the Fastfile parses and its lanes are declared with the locked fastlane. `tests/test_fastfile.rb` evaluates the Fastfile against stand-ins for the fastlane actions and Spaceship it uses, and checks what each lane would send to Google Play and App Store Connect (tracks, status, rollout, promotion, skipped uploads, release notes). It needs only Ruby's bundled `minitest`. The stand-ins can't prove how fastlane itself behaves, so still review Fastfile changes carefully.

## Testing without touching the stores

| What you changed | How to test it |
|---|---|
| `scripts/lifetime.py` | Add or update a test in `tests/test_lifetime.py`. `FakeLifeTime` stands in for the API, and the helpers build fake AAB / IPA files (`proto_manifest`, `mobileprovision`). |
| Binary inspection | `python3 scripts/lifetime.py inspect path/to/app.aab` (or `.ipa`) on a local file. No network. |
| Input resolution | `python3 scripts/lifetime.py plan --platform ios --environment "Testing"`. No network. |
| `fastlane/Fastfile` | Add or update a test in `tests/test_fastfile.rb` and run `ruby tests/test_fastfile.rb`. `Lanes` records every store call; `Spaceship::ConnectAPI.apps` / `.uploads` script App Store Connect. |
| `config/app.json` | `python3 scripts/lifetime.py check-config`, plus the unit tests. |
| Workflows | `actionlint`, then review the diff. See below. |

### Maintainers: testing against real systems

Beyond this, only a maintainer should decide to run anything against real systems, in this order of risk:

1. `lifetime.py status` or `lifetime.py fetch --dry-run` locally, with a LifeTime token: read-only LifeTime calls. `fetch --dry-run` gets the download link but downloads nothing.
2. The **Check connections** workflow: read-only, but uses the production store credentials.
3. **Release to stores** with `dry_run`: downloads and verifies the real binaries, calls Google Play with `validate_only` (Google Play validates the upload, version code and track but nothing is committed), and makes no App Store Connect call. Nothing is published or archived.

Never dispatch **Release to stores** without `dry_run` to test a change.

## Changing workflows safely

- **Pin actions to a full commit SHA** with the version as a comment, like the existing steps:
  `uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5.1.0`. Never use a tag or branch.
- **Keep `persist-credentials: false`** on every `actions/checkout`, so the `GITHUB_TOKEN` isn't left in `.git/config` for later steps.
- **Least privilege.** Keep the top-level `permissions: contents: read`. Grant more only to the job that needs it (today only `archive` has `contents: write`).
- **No expression injection.** Pass `inputs.*`, step outputs and secrets to `run:` scripts through `env:`, then use `"$VAR"` in the script. Don't write `${{ inputs.x }}` inside `run:`.
- **Scope secrets to one step.** Put secrets in the `env:` of the step that uses them, not at job or workflow level.
- **Validate free-text inputs** before use, as the `plan` job does for `android_rollout`.
- **Environment gate.** If you add a new path to production (a new track or target), make its job select the `store-production` environment.
- **Don't add `pull_request_target`** or any trigger that runs repository code with secrets for untrusted contributors.

## Changing Ruby dependencies

[Dependabot](.github/dependabot.yml) proposes weekly updates for the pinned actions and the gems, after a 7-day cooldown.

CI installs with `BUNDLE_FROZEN=true`, so it uses exactly the gems in `Gemfile.lock` (checksums verified) and fails if the lockfile doesn't match the `Gemfile`. Jobs that hold store keys install without the shared gem cache. After editing the `Gemfile` (or to update fastlane):

```bash
bundle lock --update fastlane     # only when updating fastlane; skip after a plain Gemfile edit
bundle lock --add-checksums       # regenerate the lockfile with gem checksums
bundle exec fastlane lanes
```

Commit `Gemfile` and `Gemfile.lock` together, and keep the `CHECKSUMS` section in the lockfile. Read the fastlane changelog for the versions you skip: `tests/test_fastfile.rb` stands in for fastlane, so it can't catch a change in fastlane's own behaviour.

## Pull requests

- Branch from `main`; one topic per PR.
- Fill in the [pull request template](.github/pull_request_template.md).
- **Validate** must pass.
- Update the docs that describe what you changed: [README.md](README.md), [AGENTS.md](AGENTS.md), and the relevant file in [docs/](docs/). Contracts (CLI outputs, Fastfile environment, inputs) live in AGENTS.md and must stay accurate.
- Never include secrets, key files, binaries or real store responses in a PR, its description or its logs.
- Add a line to [CHANGELOG.md](CHANGELOG.md) for user-visible changes, under an `## [Unreleased]` heading at the top (add the heading if it isn't there).
- [.github/CODEOWNERS](.github/CODEOWNERS) ships as a placeholder; in your own copy, set your owners there.

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <summary in the imperative>
```

- **type**: `feat`, `fix`, `docs`, `chore`, `ci`, `test`, `refactor`.
- **scope** (optional): `android`, `ios`, `lifetime`, `workflows`, `deps`.

Examples: `feat(android): pass changelogs during track promotion`, `docs: document track promotion`.

## Security

See [SECURITY.md](SECURITY.md) to report a problem. If a secret may have leaked, rotate it right away using [docs/SETUP.md](docs/SETUP.md).
