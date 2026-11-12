
# Release runbook

1. Update `CHANGELOG.md`; set the same version in both Python projects, Gradle, capabilities output, and fixtures.
2. Run every command in `CONTRIBUTING.md` on Linux and Windows and run package installation smoke tests.
3. Publish both Python distributions to TestPyPI and the Maven artifacts to a local staging repository; verify consumers.
4. Create and push an annotated `vX.Y.Z` tag. The protected release workflow builds artifacts again, attests them, publishes through protected environments, and creates the GitHub Release only after all publishers succeed.
5. Never upload an artifact built from a dirty tree or replace an existing version.
