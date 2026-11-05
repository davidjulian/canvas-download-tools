# Privacy and security

Run only on your own approved computer with authorized instructor access. The app opens a separate persistent browser profile. It does not request, extract or publish a Canvas API token, password or cookie.

The interface binds to `127.0.0.1` on a random port, requires a random session token for API requests, checks Host and Origin, rejects foreign-origin actions, disables browser caching and framing, and serves only its explicitly listed interface assets. Do not expose or forward that local port. The browser's temporary debugging endpoint is also bound to loopback and closed when its owned window closes.

Downloaded attachments, filenames, private manifests, CSVs and browser profiles contain identifying information. They are not automatically encrypted or de-identified. Default downloads and chosen folders must meet your institution's data handling requirements. Closing the dedicated browser is not the same as signing out; its private profile can retain sign-in state.

No AI processing, student-data uploads, screenshots, HAR files, traces, telemetry or remote error logging are included. Normal Canvas and institutional sign-in traffic still occurs. Reports show counts and fixed error categories. Do not post student records, screenshots with names, private CSVs, signed URLs or local session links in GitHub issues. Reproduce problems with invented information.

The release builder includes only named application files, interface assets, documentation, workflow definitions and invented-data tests. It never walks the Downloads folder, application data, virtual environment or Git directory. Inspect the generated ZIP before publication. Do not create a release by compressing your entire working folder.

Use Stop or Quit local app if the selected student, displayed attempt or file association is wrong. A first-file review does not establish that every later file is complete. Check the result folder and private collection results after the run.

Both API namespaces require the same per-launch token and Host/Origin checks. Only one collection may run at a time. Switching tools does not reuse the other collector's browser or profile; quitting stops both. Shared preferences contain only Canvas address, output base and browser choice. Log review and summaries can display student names locally and must remain private.
