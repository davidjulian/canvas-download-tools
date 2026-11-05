# Publish Canvas Download Tools

Suggested repository: `davidjulian/canvas-download-tools`. Keep the earlier applications/releases as working baselines.

1. Create the new repository without an automatic license. Source sharing is prepared; license selection is deferred.
2. Push this clean source repository. Do not add environments, records, profiles or outputs.
3. Run included Windows and Mac checks. The workflow uses windows-latest, macos-15 and macos-15-intel. Complete real Mac trials in both modes as described in VALIDATION.md.
4. Build with `python build_release.py`, or download the workflow's canvas-tools-source-packages artifact. Inspect both source ZIPs and SHA256SUMS.txt.
5. Create a prerelease such as v0.2.1-rc1 and attach the ZIPs and checksums. State actual platform validation and scope. Keep it a prerelease until target-platform checks are complete.
6. Optionally enable GitHub Pages from docs on the default branch. Links target the suggested repository; update them if the name changes. Only setup instructions are hosted, not the local app server or student records.

The manually dispatched workflow can prepare a draft prerelease after all tests pass. It does not publish automatically. Source packages require Python plus installed Chrome/Edge; they are not signed standalone applications.
