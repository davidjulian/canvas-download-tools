# Canvas Download Tools

[Windows download](https://github.com/davidjulian/canvas-download-tools/releases/download/v0.2.1-rc1/canvas-download-tools-windows.zip) · [Mac download](https://github.com/davidjulian/canvas-download-tools/releases/download/v0.2.1-rc1/canvas-download-tools-macos.zip) · [Release notes](https://github.com/davidjulian/canvas-download-tools/releases/tag/v0.2.1-rc1)

One local app with two tools for instructors using Canvas New Quizzes:

| Tool | Use it for |
| --- | --- |
| **Quiz activity logs** | Collect View Log text across Moderate student pages, choose named or coded output, and produce a local descriptive focus-event summary. |
| **Submitted files** | Download a file in SpeedGrader, move to the next student and repeat, with the student's surname at the beginning of each filename. |

**Version 0.2.1 — release candidate.** The collectors are preserved behind a shared local interface. Windows checks run locally. Mac support and Apple silicon/Intel CI jobs are included; a real Mac trial remains required before a stable release. The earlier standalone applications are unchanged.

## Install and launch

Requires Python 3.10+ and installed Microsoft Edge or Google Chrome. Windows 11+ and macOS 14+ are the target platforms. The interface opens in your usual browser; collection uses its own dedicated Chrome or Edge window. Safari is not supported for collection. No Canvas API token is requested. Internet access is needed to install the pinned Playwright dependency.

**Windows:** extract `canvas-download-tools-windows.zip`, open **Install.cmd** once, then **Start App.cmd**. Edge is selected initially.

**Mac:** extract `canvas-download-tools-macos.zip`, open **Install.command** once, then **Start App.command**. Chrome is selected initially. Leave Terminal open while the app runs; use **Quit local app** to finish. The source package is not a signed `.app` bundle. If a launcher opens as text, run `bash Install.command`, then `bash "Start App.command"` in its extracted folder. For Python certificate errors, use Python's Install Certificates.command under Applications and retry. Institutional security settings are not disabled by these scripts.

## Shared setup

Choose a tool on the opening screen or in the navigation at the top. Enter your institution's HTTPS Canvas home address (the example is `https://elearning.ufl.edu/`) and choose the output folder and installed browser. These settings are remembered and carry between tools, including before a run starts. Downloads is the initial output folder: Windows's configured system Downloads directory or `~/Downloads` on Mac. Browse and pasted folder paths are supported. The app does not read customized browser download preferences.

Only one collection can run at a time. Finish or Stop before switching tools. Switching closes a completed tool's retained Canvas window, preserving its files, results and sign-in profile. Each tool keeps its own profile and startup method to preserve the working collection behavior; you may need to sign in once in each. **Quit local app** closes both tools and their owned Canvas windows.

Choose institutionally approved local storage outside OneDrive and the app/profile folders. Each collection creates a fresh output folder; existing files are not overwritten.

## Quiz activity logs

1. Choose **Quiz activity logs**. Select **Coded logs** or **Named logs**, and optionally enter a quiz/folder label.
2. Select **Open Canvas**. Sign in in the separate window, open the completed New Quiz and go to **Build → Moderate**. Set the desired filters and page size. Keep one Moderate list open.
3. Select **I'm on Moderate — find logs**. The app scans recognized numbered student pages, keeps the filters and returns to page 1 for a combined name review. Discovery opens no logs.
4. Review the proposed names, check the review box and start. The app downloads the first log and pauses.
5. Check that file against Canvas. In coded mode, use the private name key to verify the ID/name association. In named mode, check the name in the file. Check the first-log review box and collect the remaining logs.
6. Review the output folder, exported ZIP and local event summary. Stop preserves completed files and creates a partial archive where possible.

Coded logs use random record IDs and attempt to remove identifiers from the sharing copies; identified originals and the name key remain in `private/`. This does not guarantee de-identification or determine institutional data classification. Named logs retain names and original dates/times and have no coded ID key. The summary includes student names in both modes and remains private. Formula-leading CSV fields are protected.

The descriptive summary reports elapsed time and paired focus/away events. Focus events do not reveal another website or establish cheating or AI use. Only rendered event text is captured; hidden attempts, virtual lists, expanded content and pagination *within a log* are outside scope. A first-log check does not establish that every later capture is complete.

## Submitted files

1. Choose **Submitted files**. Leave File type blank for all types, or enter one extension such as `pdf`, `docx` or `model`.
2. Select **Open Canvas**. Sign in, open SpeedGrader for the completed New Quiz, keep the student already displayed (normally the first), or go to the first real student you want to process. Select the **latest submission** and wait for its upload to appear. The quiz tab may remain open. Select **SpeedGrader is ready**.
3. If no saved entry exists, **Choose the student name control** is checked by default. Checking it always requests a fresh selection, including when Canvas has a built-in or hidden selector. When prompted, click the **currently displayed student's name field once** in Canvas, without changing students. The location is remembered locally. There is no picker-cycling interface. If the saved location changes, click the field again. Student name setup forces fresh identification.
4. When prompted, click **one submitted filename** in Canvas that downloads the actual file.
5. Open the saved file or folder. Check the contents and student association, check the review box, then select **Checked — download remaining files**.
6. Leave Canvas alone while the app verifies its automatic click and continues through the current list, stopping when Next is disabled or the list returns to the first student.

Filenames use `Last_First_original_filename.ext`. Original extensions, ordinary spaces and capitalization are preserved. Unsafe characters and excessive filesystem lengths are adjusted. Duplicates receive ` (2)` only when needed. A comma in Canvas supplies surname order; otherwise the final word is treated as the surname, so check compound surnames locally.

Test Student and Student, Test are skipped, including an empty final test submission. Recognized No submission/No answer notices are skipped. An unrecognized empty real submission stops collection for review.

Scope is **one learned file per student in the current filtered list**, using the displayed attempt. All file types does not mean all attachments. Multiple attachments and earlier attempts are not collected automatically. The app leaves attempt selection alone and does not independently verify recency; select the latest before starting. Changed identity, ambiguous controls, unexpected types or a mismatching first automatic download stop the run and preserve completed files. There is no automatic resume.

## Local records and privacy

There are no AI, analytics, screenshots, traces, telemetry or student-data upload features. Normal Canvas, file-delivery and institutional sign-in traffic still occurs. Files, private records and sign-in state remain local. They are not automatically encrypted or de-identified. Follow institutional access, storage and retention rules; these controls do not certify FERPA compliance.

App settings and profiles live under `%LOCALAPPDATA%\CanvasDownloadTools` on Windows or `~/Library/Application Support/CanvasDownloadTools` on Mac. Tools use separate profile subfolders. Remembered picker data contains structural paths and hashes rather than names or IDs. File manifests, mappings and results stay in private app storage; log private records stay in their selected run folder. Closing a browser is not the same as signing out.

See [SECURITY.md](SECURITY.md). Never include records, profiles, private CSVs, identifying screenshots or session links in GitHub issues or software ZIPs.

## Development and release

Run `python -m unittest discover -s tests -q`. Set `CANVAS_RUN_BROWSER_TESTS=1` to run invented-data browser checks with installed Chrome/Edge; `CANVAS_TEST_BROWSER=chrome` or `msedge` selects the channel. Fixture requests are intercepted or blocked.

Run `python build_release.py` for Windows and Mac source ZIPs and checksums under `dist/`. An explicit allowlist excludes environments, profiles, downloads and Git history and preserves Mac launcher executable bits. See [VALIDATION.md](VALIDATION.md) for actual checks and [PUBLISHING.md](PUBLISHING.md) for GitHub setup. The project uses the [0BSD license](LICENSE), permitting use, modification and redistribution without an acknowledgement or notice-retention requirement. Dependencies retain their own licenses.

After updating an already running copy, finish or stop the collection, use Quit local app, then reopen Start App. Refreshing the browser page does not reload the Python downloader. Submitted files shows a restart notice when newer interface files meet an older backend.

The Submitted files Collection panel shows only the current step. Its five steps separate opening SpeedGrader, selecting the name field, clicking the file link, reviewing the first file and downloading the rest. Edge may briefly show its temporary download location; use Open download folder in the app for the final renamed copy.

## About and contact

The main-page About dialog explains both tools, local data handling, and development by David Julian using OpenAI Codex. Codex helped develop the software; the running app does not send Canvas records to Codex or another AI service. Contact David Julian at [djulian@ufl.edu](mailto:djulian@ufl.edu). Use invented examples in problem reports, not student records or sign-in information.
