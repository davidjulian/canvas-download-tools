# Canvas Download Tools validation

The standalone quiz log and SpeedGrader applications remain preserved. Their collectors are combined through task-specific adapters rather than a shared navigation algorithm. SpeedGrader retains normal browser startup followed by delayed attachment; logs retain the tested Moderate workflow.

Combined checks cover shared settings, one active collector, safe task switching, retained-browser shutdown, both protected API namespaces and Quit closing both tools. Browser checks exercise first-download review and complete collections with invented submissions, including navigation to the other task. Existing log tests preserve coded/named modes, pagination, private keys, descriptive summaries and partial archives. File tests preserve delayed frames, duplicates, direct name selection, test-student skipping, filters and safe paths.

Actual local results are recorded after completed checks. Configuring a Mac job does not establish that it passed. Mac dialogs, application paths and Finder calls can be tested with mocks on Windows; real Mac installation, SSO and a small instructor-reviewed collection in each mode are still required before a stable Mac release.

Use a prerelease until the combined interface has been tried against real Canvas on target platforms. Never attach student records to CI artifacts or issues. Software packages contain only allowlisted source, invented tests, documentation and launchers.

## Completed local checks

The full Windows run executed 252 tests. All collection and interface assertions passed; one native-browser test failed during cleanup because Windows still held a temporary profile database file. Only the test cleanup deadline was extended, from five to fifteen seconds. The five native-browser checks then all passed on rerun. The remaining 247 checks passed in the full run. The current non-browser suite also passes, with browser cases explicitly skipped when not enabled.

Both complete workflows were exercised through the combined routes using invented submissions, including the first-download pause, progress and switching tools after completion. All three screens were checked at desktop and narrow widths with no page errors or horizontal overflow. Both source ZIPs match the explicit 56-file allowlist; no additional files are tracked. A freshly extracted package imports both collectors independently. Mac launchers passed shell syntax checks and retain executable bits; actual Mac execution remains pending.
