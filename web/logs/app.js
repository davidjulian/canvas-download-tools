"use strict";
(() => {
  const token = sessionStorage.getItem('canvas-tools-token') || location.hash.slice(1);
  const $ = id => document.getElementById(id);
  let state = {phase: "idle", run_id: ""};
  let renderedReview = "";
  let renderedCheck = "";
  let polling = true;
  let busy = false;
  let modeRestored = false;
  let browserRestored = false;
  let labelRun = null;
  let summaryKey = "";
  let summarySort = {key: "position", desc: false};
  let labelWarningRun = "";
  $("about-open").addEventListener("click", () => $("about-dialog").showModal());
  $("about-close").addEventListener("click", () => $("about-dialog").close());
  const terminal = new Set(["idle", "complete", "stopped", "error"]);
  const descriptions = {
    idle: ["Ready when you are", "Choose your Canvas address and output folder, then open Canvas to begin.", "Not started"],
    opening: ["Opening your Canvas browser", "A dedicated window will open. Your sign-in stays in that browser.", "Opening"],
    navigate: ["Open your quiz’s Moderate page", "Use the separate Canvas window, then return here to find the logs.", "Your turn"],
    finding: ["Finding logs across Moderate pages", "The app scans the recognized student pages, keeps your filters and returns to page 1 for review. Leave Canvas alone while it works.", "Finding logs"],
    review: ["Review the student names", "Confirm who each View Log belongs to before collection begins.", "Your turn"],
    capturing: ["Collecting your logs", "The app is opening logs in their displayed order and saving each one locally.", "Collecting"],
    checking: ["Check the first downloaded log", "Collection is paused while you check the private name key and downloaded text.", "Your turn"],
    finishing: ["Finishing your local export", "Preserving the text files and creating the export ZIP.", "Finishing"],
    stopping: ["Stopping collection", "Waiting for the current browser operation to end. Completed files will be preserved.", "Stopping"],
    complete: ["Collection finished", "The reviewed View Log controls across the recognized Moderate pages have been processed.", "Finished"],
    stopped: ["Collection stopped", "Any completed files remain in your output folder. A new run starts from the first student.", "Stopped"],
    error: ["Collection needs attention", "Any completed files are preserved. Check the message below before starting a new run.", "Needs attention"]
  };
  async function api(path, body) {
    const options = {cache: "no-store", headers: {Authorization: `Bearer ${token}`}};
    if (body !== undefined) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
    const response = await fetch((location.pathname.startsWith('/logs/') ? '/logs' : '') + path, options);
    if (!response.ok) {
      if (response.status === 403) throw new Error("This session is not authorized. Reopen the app using its Start App launcher.");
      let result;
      try { result = await response.json(); } catch (_) {}
      throw new Error(result?.error || "The local app could not complete that action.");
    }
    return response.json();
  }
  function localError(message) {
    $("local-error").textContent = message;
    $("local-error").hidden = !message;
  }
  function show(id, visible) { $(id).hidden = !visible; }
  function elapsed(value) {
    if (value === null || value === undefined) return "—";
    return [Math.floor(value / 3600), Math.floor(value / 60) % 60, value % 60].map(n => String(n).padStart(2, "0")).join(":");
  }
  function renderSummary(rows) {
    const key = JSON.stringify([rows, summarySort]);
    if (key === summaryKey) return;
    summaryKey = key;
    const ordered = [...rows].sort((a, b) => {
      const x = a[summarySort.key], y = b[summarySort.key];
      if (x === null && y !== null) return 1;
      if (y === null && x !== null) return -1;
      const comparison = typeof x === "string" ? x.localeCompare(y) : (x ?? 0) - (y ?? 0);
      return (summarySort.desc ? -comparison : comparison) || a.position - b.position;
    });
    $("summary-rows").replaceChildren();
    for (const row of ordered) {
      const tr = document.createElement("tr");
      const name = document.createElement("td");
      const label = document.createElement("div"); label.textContent = row.student_name; name.append(label);
      const position = document.createElement("small"); position.className = "hint"; position.textContent = `View Log ${row.position}`; name.append(position);
      if (row.notes.length) {
        const details = document.createElement("details"), summary = document.createElement("summary"), note = document.createElement("p");
        summary.textContent = "Check log"; note.textContent = row.notes.join(" "); note.className = "hint";
        details.append(summary, note); name.append(details);
      }
      tr.append(name);
      const elapsedCell = document.createElement("td");
      const elapsedValue = document.createElement("div"); elapsedValue.textContent = elapsed(row.total_elapsed_seconds);
      elapsedCell.append(elapsedValue);
      if (row.elapsed_basis === "last visible event" && row.total_elapsed_seconds != null) {
        const estimate = document.createElement("small"); estimate.className = "hint";
        estimate.textContent = "Estimated from last timed event"; elapsedCell.append(estimate);
      }
      const stopCell = document.createElement("td"); stopCell.textContent = String(row.stopped_viewing_events ?? "—");
      tr.append(elapsedCell, stopCell);
      const mean = document.createElement("td"), value = document.createElement("div"), pairs = document.createElement("small");
      value.textContent = row.average_away_seconds === null ? "—" : row.average_away_seconds.toFixed(2);
      pairs.className = "hint"; pairs.textContent = row.stopped_viewing_events === null ? "Pairing unavailable" : `${row.paired_intervals} paired; ${row.unpaired_stop_events} unpaired stops`;
      mean.append(value, pairs); tr.append(mean); $("summary-rows").append(tr);
      for (const metric of [row.total_away_seconds, row.percent_elapsed_away, row.longest_away_seconds]) {
        const td = document.createElement("td"); td.textContent = metric == null ? "—" : String(metric); tr.append(td);
      }
    }
    document.querySelectorAll("[data-summary-sort]").forEach(button => {
      button.parentElement.setAttribute("aria-sort", button.dataset.summarySort === summarySort.key ? (summarySort.desc ? "descending" : "ascending") : "none");
    });
  }
  function render(next) {
    state = next;
    if (!browserRestored) {
      $("browser").value = state.default_browser || "msedge";
      browserRestored = true;
    }
    const phase = state.phase;
    const active = !terminal.has(phase);
    if (labelRun === null || (state.run_id && labelRun !== state.run_id)) {
      $("folder-label").value = state.folder_label || "";
      $("keep-canvas-open").checked = !!state.keep_browser_open;
      if (state.canvas_url) $("canvas-url").value = state.canvas_url;
      if (state.output_base) $("output").value = state.output_base;
      labelRun = state.run_id;
    }
    if (!modeRestored || active) {
      $(state.export_mode === "named" ? "mode-named" : "mode-coded").checked = true;
      modeRestored = true;
    }
    for (const id of ["mode-coded", "mode-named"]) $(id).disabled = active || busy;
    updateModeNote();
    const named = state.export_mode === "named";
    const [title, description, status] = descriptions[phase] || descriptions.error;
    $("stage-title").textContent = title;
    $("stage-description").textContent = description;
    $("status").textContent = status;
    $("error").textContent = state.error || "";
    show("error", !!state.error);
    const diagnostic = state.diagnostic || {};
    show("diagnostic-details", Object.keys(diagnostic).length > 0);
    $("diagnostic-text").textContent = Object.keys(diagnostic).length ? JSON.stringify(diagnostic, null, 2) : "";
    $("moderate").textContent = state.error ? "Try finding logs again" : "I’m on Moderate — find logs";
    for (const id of ["canvas-url", "output", "folder-label", "keep-canvas-open", "browse", "browser", "launch"]) $(id).disabled = active || busy;
    const supportsRunOptions = typeof state.folder_label === "string" && typeof state.keep_browser_open === "boolean" && Array.isArray(state.summary_rows) && state.pagination_supported === true && state.label_warning_supported === true && state.moderate_retry_supported === true;
    show("update-note", !supportsRunOptions);
    if (!supportsRunOptions) for (const id of ["launch", "folder-label", "keep-canvas-open"]) $(id).disabled = true;
    $("launch").textContent = phase === "idle" ? "Open Canvas" : state.browser_open ? "Use open Canvas for a new run" : "Open Canvas for a new run";
    show("welcome", phase === "idle");
    show("navigate", phase === "navigate");
    show("review", phase === "review");
    show("first-check", phase === "checking");
    show("open-key-check", !named && !!state.key_ready);
    $("open-first").textContent = named ? "Open first named log" : "Open first coded log";
    $("first-check-help").textContent = named
      ? "Compare the student name and downloaded text with Canvas. Original dates and times are retained. This mode has no coded ID or name key."
      : "Use the private name key to confirm the ID belongs to the correct student. Compare the downloaded event text and elapsed times with Canvas. Start and finish dates and clock times are omitted.";
    $("first-check-label").textContent = named ? "I checked the student name and downloaded text against Canvas." : "I checked the ID in the private key and the downloaded text against Canvas.";
    show("stop", active);
    $("stop").disabled = ["stopping", "finishing"].includes(phase) || busy;
    show("progress-section", state.total > 0 && phase !== "review");
    show("output-section", state.total > 0 || state.saved > 0);
    show("result", ["complete", "stopped", "error"].includes(phase) && state.total > 0);
    const step = ["idle", "opening", "navigate", "finding"].includes(phase) ? 1 :
      phase === "review" ? 2 : phase === "checking" || (phase === "capturing" && state.saved === 0) ? 3 : 4;
    for (let i = 1; i <= 4; i++) {
      $("step" + i).classList.toggle("active", i === step);
      $("step" + i).classList.toggle("done", i < step);
      if (i === step) $("step" + i).setAttribute("aria-current", "step");
      else $("step" + i).removeAttribute("aria-current");
    }
    if (phase === "review" && renderedReview !== state.run_id) {
      renderedReview = state.run_id;
      $("names-reviewed").checked = false;
      $("name-rows").replaceChildren();
      for (const row of state.names) {
        const tr = document.createElement("tr");
        const position = document.createElement("td");
        position.textContent = row.position;
        const pageLabel = document.createElement("small");
        pageLabel.className = "hint"; pageLabel.textContent = `Page ${row.moderate_page || 1}`;
        position.append(document.createElement("br"), pageLabel);
        const cell = document.createElement("td");
        const input = document.createElement("input");
        input.type = "text"; input.value = row.name; input.maxLength = 200;
        input.autocomplete = "off";
        input.dataset.position = row.position;
        input.setAttribute("aria-label", `Student name at position ${row.position}`);
        cell.append(input); tr.append(position, cell); $("name-rows").append(tr);
      }
      $("count-label").textContent = `${state.total} View Log controls found across ${state.page_count || 1} Moderate page${state.page_count === 1 ? "" : "s"}`;
    }
    if (phase === "checking" && renderedCheck !== state.run_id) {
      renderedCheck = state.run_id;
      $("first-reviewed").checked = false;
    }
    $("start").disabled = !$("names-reviewed").checked || busy;
    $("checked").disabled = !$("first-reviewed").checked || busy;
    const percent = state.total ? Math.round(state.saved / state.total * 100) : 0;
    $("progress-text").textContent = `${state.saved} of ${state.total} logs saved`;
    $("percent").textContent = `${percent}%`;
    $("progress").max = state.total || 1;
    $("progress").value = state.saved;
    $("progress-note").textContent = active ? `Moderate page ${state.current_page || 1} of ${state.page_count || 1}. Please leave the dedicated Canvas window alone while collection runs.` : "Files remain in the output folder below.";
    $("output-path").textContent = state.output;
    $("archive-note").textContent = named
      ? (state.archive_ready ? "named-logs.zip contains student names and original dates, times and event text. No key was created." : "Named downloads are in logs/. No student coding or name key is used.")
      : (state.archive_ready ? "coded-logs.zip contains the downloaded text files. The private name key is excluded." : "Downloaded text is in logs/. The name key and full captures stay in private/.");
    $("output-privacy").textContent = named ? "These downloads contain identified student records. Use only storage and services authorized for their required classification and follow institutional privacy and FERPA rules." : "Keep private/ and name_key.csv private. Review coded files before sharing: identifying details can remain. This app does not certify FERPA compliance.";
    $("result-description").textContent = `${state.saved} of ${state.total} reviewed logs saved across ${state.page_count || 1} Moderate page${state.page_count === 1 ? "" : "s"}. ${state.archive_ready ? "Your download ZIP is ready." : "Check the output folder for completed files."}${state.browser_open ? " Canvas remains open. Open another quiz and go to Build → Moderate, update the folder label, then select Use open Canvas for a new run." : ""}`;
    show("open-key", !!state.key_ready);
    show("duplicates", state.warning_count > 0);
    const rows = state.summary_rows || [];
    show("event-summary", rows.length > 0);
    renderSummary(rows);
    $("open-summary").disabled = !state.summary_ready || busy || active;
    $("summary-error").textContent = state.summary_error || "";
    show("summary-error", !!state.summary_error);
    $("activity").textContent = phase === "finding" && state.current_page ? `Scanning Moderate page ${state.current_page}; ${state.discovered_logs || 0} log controls found so far.` : active ? (phase === "checking" || phase === "review" || phase === "navigate" ? "Waiting for your next step." : "Local collection is running.") : "No collection is running.";
  }
  async function refresh() {
    render(await api("/api/state"));
  }
  function updateModeNote() {
    $("mode-note").textContent = $("mode-named").checked
      ? "Names and original dates and times are retained. Use only an environment authorized for identified student records."
      : "Review coded files before sharing. The private folder still contains identifying records.";
  }
  for (const id of ["mode-coded", "mode-named"]) $(id).addEventListener("change", updateModeNote);
  document.querySelectorAll("[data-summary-sort]").forEach(button => button.addEventListener("click", () => {
    const key = button.dataset.summarySort;
    summarySort = {key, desc: summarySort.key === key ? !summarySort.desc : key !== "student_name"};
    renderSummary(state.summary_rows || []);
  }));
  async function action(name, extra = {}) {
    if (busy) return;
    busy = true; localError(""); render(state);
    try {
      await api("/api/action", {action: name, run_id: state.run_id, ...extra});
      await refresh();
    } catch (error) { localError(error.message); }
    finally { busy = false; render(state); }
  }
  const normalizedLabel = value => value.trim().replace(/\s+/g, " ").toLowerCase();
  function launchCollection(reuseLabelRun = "") {
    action("launch", {url: $("canvas-url").value.trim(), output: $("output").value.trim(), folder_label: $("folder-label").value.trim(), keep_browser_open: $("keep-canvas-open").checked, browser: $("browser").value, export_mode: $("mode-named").checked ? "named" : "coded", reuse_label_run_id: reuseLabelRun});
  }
  $("setup").addEventListener("submit", event => {
    event.preventDefault();
    if (busy) return;
    if (state.run_id && normalizedLabel($("folder-label").value) === normalizedLabel(state.folder_label)) {
      labelWarningRun = state.run_id;
      $("previous-folder-label").textContent = state.folder_label.trim() || "(no quiz label)";
      $("label-warning-dialog").showModal();
      return;
    }
    launchCollection();
  });
  $("change-folder-label").addEventListener("click", () => {
    $("label-warning-dialog").close();
    $("folder-label").focus(); $("folder-label").select();
  });
  $("reuse-folder-label").addEventListener("click", () => {
    $("label-warning-dialog").close();
    launchCollection(labelWarningRun);
  });
  $("browse").addEventListener("click", async () => {
    $("browse").disabled = true; localError("");
    try { const result = await api("/api/browse", {}); if (result.folder) $("output").value = result.folder; }
    catch (error) { localError(error.message); }
    finally { $("browse").disabled = !terminal.has(state.phase); }
  });
  $("moderate").addEventListener("click", () => action("moderate"));
  $("start").addEventListener("click", () => {
    const names = [...$("name-rows").querySelectorAll("input")].map(input => ({position: Number(input.dataset.position), name: input.value}));
    action("start", {reviewed: $("names-reviewed").checked, names});
  });
  $("checked").addEventListener("click", () => action("checked"));
  $("stop").addEventListener("click", () => action("stop"));
  for (const id of ["names-reviewed", "first-reviewed"]) $(id).addEventListener("change", () => render(state));
  for (const [id, path] of [["open-folder", "/api/open-folder"], ["open-first", "/api/open-first"], ["open-key", "/api/open-key"], ["open-key-check", "/api/open-key"] , ["open-summary", "/api/open-summary"]]) {
    $(id).addEventListener("click", async () => {
      localError("");
      try { await api(path, {}); } catch (error) { localError(error.message); }
    });
  }
  $("quit").addEventListener("click", async () => {
    try {
      await api("/api/quit", {});
      window.dispatchEvent(new Event("canvas-tools-closed"));
      polling = false;
      document.querySelectorAll("button,input,select").forEach(element => element.disabled = true);
      $("stage-title").textContent = "Local app closed";
      $("stage-description").textContent = "Any active collection is stopping and preserving completed files. You can close this tab.";
      $("status").textContent = "Closed";
      $("activity").textContent = "Use the Start App launcher to reopen.";
    } catch (error) { localError(error.message); }
  });
  async function poll() {
    if (!polling) return;
    try { await refresh(); }
    catch (_) { localError("The local app is unavailable. Reopen it with the Start App launcher if it has closed."); }
    if (polling) setTimeout(poll, 750);
  }
  if (!token) {
    localError("Open this interface using the Start App launcher. The session link is required to access local records.");
    document.querySelectorAll("button,input,select").forEach(element => element.disabled = true);
  } else { poll(); }
})();
