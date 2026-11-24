## Summary

<!-- What does this change and why? Link the issue if there is one. -->

## Type of change

- [ ] Bug fix
- [ ] New feature or behaviour change
- [ ] Workflow / CI change (`.github/workflows/`)
- [ ] Store lanes (`fastlane/Fastfile`)
- [ ] App configuration (`config/app.json`)
- [ ] Dependencies (`Gemfile`, `Gemfile.lock`)
- [ ] Documentation only

## Checklist

- [ ] `python3 -m unittest discover -s tests -v` passes, with tests added or updated for changed behaviour
- [ ] `python3 scripts/lifetime.py check-config` passes
- [ ] `ruby tests/test_fastfile.rb` passes, with tests added or updated (if `fastlane/Fastfile` changed)
- [ ] `actionlint` passes (if workflows changed)
- [ ] `bundle exec fastlane lanes` passes; `Gemfile.lock` regenerated with `bundle lock --add-checksums` (if the Gemfile changed)
- [ ] New actions are pinned to a full commit SHA and checkouts keep `persist-credentials: false`
- [ ] No secrets, tokens, key files, binaries or store responses in the code, this description or the logs
- [ ] Docs updated (README.md, AGENTS.md, docs/) where behaviour, inputs or contracts changed
- [ ] I did **not** run `release.yml` (Release to stores) to test this change

## How was this tested?

<!-- Commands you ran and their result. If a maintainer ran Check connections or a dry run, say so here. -->
