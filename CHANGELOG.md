# Changelog

All notable changes to this template are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

Copies of the template don't receive updates automatically: compare your copy with the tag of the release you want and bring the changes over.

## [1.0.0] - 2026-11-24

Initial public release.

### Added

- **Release to stores** workflow: fetches the MABS native build (AAB / IPA) of an OutSystems 11 app from LifeTime (REST API v2) and publishes it with fastlane.
- Google Play: `internal`, `alpha`, `beta` and `production` tracks, staged rollouts, drafts and release notes. A version code already on Google Play is promoted from the track that holds it, or has its rollout changed on the same track.
- App Store Connect: TestFlight upload and App Store review submission, always with manual release. A build already in App Store Connect is not uploaded again. "What's New" goes to the languages the App Store version already has. Export-compliance answer through `IOS_USES_NON_EXEMPT_ENCRYPTION`.
- Checks before any upload: tagged LifeTime version, package / bundle ID, safe version strings, App Store distribution profile (development, ad-hoc, enterprise, unsigned and expired profiles are refused).
- Dry run that downloads and verifies both binaries and validates the Google Play upload without committing it.
- Archive of released binaries in GitHub Releases with SHA-256. Refused in public repositories, where the binaries are not kept as workflow artifacts either.
- **Check connections** read-only workflow (LifeTime, Google Play, App Store Connect; only the platforms in `config/app.json`) and **Validate** workflow (config check, unit tests, actionlint, Fastfile load and decision tests).
- `scripts/setup-secrets.sh` to set the secrets and variables with the GitHub CLI.
- Template placeholders in `config/app.json`; `fetch` and `status` refuse to run until they are replaced.
- Security hardening: SHA-pinned actions, step-scoped secrets, `persist-credentials: false`, LifeTime token sent only to the LifeTime host over HTTPS, single-line log annotations, frozen gems with checksums, no shared gem cache in jobs holding store keys, Dependabot.
- Documentation: setup, releasing guide, troubleshooting, architecture, version archiving, security policy, contribution guide.

[1.0.0]: ../../releases/tag/v1.0.0
