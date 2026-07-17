# PyBox — Technical Report

**Version:** 1.0 (early-stage, actively developed)
**Platform:** Android 14+ (arm64-v8a), tested on Samsung Galaxy S22 Ultra (Snapdragon 8 Gen 1)
**Author/Owner:** Khan
**Report date:** July 2026

---

## 1. What PyBox Is

PyBox is a self-contained Android application that runs a full Python/Flask
backend, a native on-device LLM inference engine, and a hot-reloadable
automation system — entirely offline, entirely local, with no cloud
dependency at its core. It is not a wrapper around a cloud API. Every piece
of intelligence and automation it runs, runs on the phone itself.

The design principle running through every part of it: **nothing leaves the
device unless you explicitly authorize it to** (e.g. the optional,
OAuth-gated Google Drive connection). The default posture is offline-first,
loopback-only, and locally sandboxed.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Android App (Kotlin)                                     │
│  ┌───────────────┐   ┌────────────────────────────────┐  │
│  │ MainActivity   │   │ LlamaEngineService (foreground)│  │
│  │ - WebView UI   │   │ - runs libllama_server.so      │  │
│  │ - health checks│   │ - loopback :8081               │  │
│  │ - JS bridge    │   └────────────────────────────────┘  │
│  └───────┬───────┘                                        │
│          │ Chaquopy (embedded CPython 3.11)                │
│  ┌───────▼─────────────────────────────────────────────┐  │
│  │ Flask backend (backend_app.py) — loopback :5000       │  │
│  │  ├─ auth.py        per-install token, private storage │  │
│  │  ├─ config.py       JSON key-value settings store      │  │
│  │  ├─ scheduler.py    SQLite-backed periodic jobs         │  │
│  │  ├─ watcher.py      polling folder watcher               │  │
│  │  ├─ plugin_loader.py  hot-reload .py from SD card          │  │
│  │  ├─ scraper.py      public web page fetch/parse             │  │
│  │  ├─ osint_tools.py  passive WHOIS/DNS/fingerprint/CT-log      │  │
│  │  └─ gdrive.py       OAuth-gated Google Drive access             │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
        ▲                                    ▲
        │ SD card (not compiled into APK)    │ GitHub Actions CI
   PyBox/plugins/*.py                   NDK + CMake + Gradle
   PyBox/inbox, PyBox/models/*.gguf     builds llama.cpp + APK
```

**Key architectural decisions and why:**

- **Flask binds to `127.0.0.1` only** — unreachable from the network, by
  construction, not by configuration that could be forgotten.
- **A per-install auth token** (in app-private storage, never on the SD
  card) gates every mutating route, because loopback is shared across all
  apps on the device — other apps *can* reach `127.0.0.1:5000` in
  principle, so the token closes that gap.
- **The LLM runs as a real native binary** (llama.cpp's `llama-server`,
  cross-compiled for arm64-v8a during CI), not a Python wrapper — this is
  what makes on-device inference viable at all on phone hardware.
- **Plugins live outside the APK**, on the SD card, and are dynamically
  imported at runtime. This exists specifically so new capability can be
  added without a GitHub Actions round-trip (10–15 minutes) for every small
  change. Plugin routes are dispatched through one pre-registered Flask
  route (`/plugins/<name>`) rather than calling `app.route()` per plugin,
  because Flask 3.x refuses new route registration after the server's
  first request — a real constraint discovered and worked around during
  development, not a design preference.
- **Every subsystem init is independently wrapped in try/except.** A
  failure in one (e.g. lost storage permission after a reinstall) cannot
  prevent the Flask server itself from starting — this was a real bug found
  and fixed: an unhandled exception in plugin setup was silently preventing
  `app.run()` from ever being reached.

---

## 3. Current Capabilities (as of this report)

### Core platform
- Embedded Python 3.11 backend (Flask), running as a persistent background
  process inside the Android app via Chaquopy.
- WebView-based UI, backend-served, extensible without touching native code.
- Admin dashboard (`/admin`) — live config editing, job/watcher management,
  plugin status, log tail, all from the phone.

### On-device AI
- llama.cpp `llama-server`, natively compiled for the device's exact CPU
  (ARMv8.2 + dotprod + i8mm), run as a background foreground service.
- Reachable via `/llm/generate`, proxied from the Flask backend.
- Fully offline: no API key, no per-token cost, no network dependency once
  a `.gguf` model is on-device.

### Automation
- **Scheduler**: SQLite-backed periodic jobs, pluggable handlers, run
  history, auto-respects a global `automation_enabled` config flag.
- **Watcher**: polling-based folder watching (no native `inotify`
  dependency), only scans folders explicitly registered — never the whole
  device.
- **Plugin system**: drop a `.py` file on the SD card, reload from the
  admin panel, it runs — no rebuild, no CI, no reinstall.

### Data & integration
- **Web scraping** (public pages only — no auth bypass, no session
  hijacking; behaves like a normal browser request).
- **Passive OSINT**: WHOIS, DNS records, HTTP security-header
  fingerprinting, Certificate Transparency subdomain search, local file
  metadata (EXIF) — deliberately excludes anything that scans or probes a
  target's live infrastructure.
- **Google Drive**: OAuth2-authorized, least-privilege scope by default
  (read-only + write-to-app-created-files-only), revocable anytime from the
  user's own Google account settings.

### Shipped example plugins
| Plugin | Function |
|---|---|
| `auto_summarize.py` | Watches an inbox folder, summarizes new text files via the on-device LLM |
| `notes_search.py` | Offline quick notes with SQLite FTS5 full-text search |
| `battery_guard.py` | Reads real battery state, pauses all scheduled automation below 20% |
| `web_watch.py` | Scheduled page-change monitoring with diff history |
| `osint_report.py` | Composite WHOIS+DNS+subdomains+fingerprint report in one call |

### Build & delivery
- GitHub Actions CI: installs Android NDK, clones llama.cpp fresh each
  build, compiles native + Kotlin + Python, produces a debug APK artifact.
- **Fixed debug signing key**, committed intentionally, so successive CI
  builds install as updates rather than requiring uninstall/reinstall —
  this was a real deployment friction point that's now solved.

---

## 4. Security & Privacy Posture

- No cloud dependency by default. The only outbound network calls PyBox
  makes on its own are: (a) web scraping/OSINT requests *you* trigger
  against targets *you* specify, and (b) Google Drive calls, only after
  explicit OAuth consent you can revoke anytime.
- Loopback-only backend + per-install token, specifically to prevent other
  apps on the same device from reaching PyBox's API.
- App-private storage (Android's sandboxed `/data/data/com.khan.pybox/files`)
  holds the auth token, OAuth tokens, and databases — inaccessible to other
  apps without root.
- "Full admin" of the Android OS is explicitly **not** something this (or
  any) sideloaded app has on a non-rooted device — documented honestly
  rather than implied. What PyBox does have: full control over its own
  automation, full access to files you grant it via Android's storage
  permission, and real background/headless execution (visible via a
  persistent notification, by Android's design, not PyBox's choice).

---

## 5. Known Limitations (honest inventory)

- **CPU-only inference** — no GPU/NNAPI acceleration wired up yet. Fine for
  small quantized models, not fast for large ones.
- **`protobuf`** (a Google Drive dependency) has no pure-Python wheel;
  relies on Chaquopy's own prebuilt-for-Android package support. Confirmed
  working in CI, but is the one dependency in the stack without a
  pure-Python fallback path.
- **Polling, not event-driven**, for file watching (no `inotify`) — a
  deliberate tradeoff for zero native dependencies, at the cost of
  sub-second reactivity (10s scan interval by default).
- **No versioning/release process yet** — currently a single rolling
  `main` branch, debug builds only. No release channel, no changelog
  automation, no semantic versioning applied.
- **No automated tests** for the Python backend or Kotlin app layer yet —
  correctness has been verified manually, build-by-build, not via a test
  suite.
- **Single-device design** — no sync/multi-device story. Everything lives
  on this one phone's storage.

---

## 6. Current Vision

PyBox is meant to be Khan's personal, sovereign automation and AI
substrate: a phone-resident platform that can run scheduled jobs, watch
for events, do on-device inference, and be extended in minutes (via
plugins) rather than hours (via CI), while keeping data under his control
by default and only reaching outward when explicitly authorized.

It is explicitly **not** trying to be a general-purpose consumer app — it's
infrastructure for one person's projects, education, and experimentation,
matching the broader pattern across Khan's other tools (KHAN-OS AEO,
FileForge, MMIS): offline-first, low-resource-aware, locally controlled.

---

## 7. Future Vision & Upgrade Plan

### Near-term (next few iterations)
- **Testing**: a real test suite for the Python backend (pytest, run in
  CI) — the highest-leverage reliability investment available right now,
  given how much manual debugging has been needed to date.
- **Versioning**: adopt semantic versioning, a CHANGELOG, and tagged
  releases instead of a single rolling debug build.
- **DAG-based automation runtime** — rather than only independent
  scheduled jobs, a proper directed-acyclic-graph executor (job B runs only
  after job A succeeds, conditional branches, retries) — mirroring the
  automation runtime already proven out in KHAN-OS AEO's Phase 4, adapted
  for PyBox's phone-resident context.
- **Event bus** — right now, scheduler/watcher/plugins are only loosely
  coupled via shared dicts. A proper internal event bus (publish domain
  events, subscribe from any plugin) would make cross-plugin composition
  (e.g. "web_watch change → trigger auto_summarize on the diff") possible
  without plugins needing to know about each other directly.

### Medium-term
- **GPU/NNAPI-accelerated inference**, once llama.cpp's Android GPU
  backend support is mature enough to integrate reliably.
- **Structured plugin manifest** (a `plugin.json` alongside each `.py`)
  declaring name, version, required permissions, and dependencies — turns
  the current "drop a file in a folder" model into something closer to a
  real, self-describing plugin ecosystem, echoing KHAN-OS AEO's Phase 8
  plugin architecture.
- **Digital twin / static analysis of PyBox's own codebase** (AST-based,
  as already built for KHAN-OS AEO Phase 6) — PyBox introspecting its own
  plugin/route/job graph to detect conflicts, orphaned handlers, or unused
  code automatically.

### Long-term
- **Human-approval-gated self-improvement loop** — PyBox proposing changes
  to its own automation (new jobs, tuned thresholds, new plugin
  combinations) based on observed usage patterns, but never auto-executing
  without explicit approval — same governing principle as KHAN-OS AEO's
  Phase 9 autonomous engineering manager.

---

## 8. Making PyBox Robust, Powerful, and "Cognitive" — Without Depending on AI

This is the most important section for long-term resilience: **the LLM
should be one capability PyBox has, not the thing PyBox depends on to
function.** Battery Guard already demonstrates this principle (it pauses
automation using plain sensor reads and config flags — zero AI involved).
The same principle should extend further:

1. **Rule engine, not just schedule + watch.** Add a small, deterministic
   condition/action rule system: `IF <event or state> THEN <action>`,
   evaluated the same tick loop that already drives the scheduler. This
   gives PyBox real decision-making — routing, filtering, conditional
   automation — without needing a model loaded, which matters on a phone
   where running the LLM has a real battery/thermal cost.

2. **Event correlation over time**, not just single-event reactions.
   Example: "if `web_watch` detects 3+ changes on the same URL within an
   hour, escalate the log level" — pattern detection over a rolling window
   is genuine intelligence that's fully deterministic and cheap to run
   constantly in the background, unlike LLM inference.

3. **Statistical/heuristic scoring** for things like OSINT reports or
   scraped data (e.g. flag domains whose WHOIS creation date is very
   recent alongside a large subdomain count as "worth a closer look") —
   useful signal from cheap arithmetic, reserving the LLM for cases that
   actually need language understanding.

4. **Graceful AI degradation as a first-class state**, not an edge case.
   Every plugin that *can* use the LLM (like `auto_summarize`) should have
   a defined non-AI fallback (e.g. store the raw file with a
   "not-yet-summarized" flag, and back-fill once the engine comes back up)
   — so Battery Guard pausing automation, or the phone simply not having
   the LLM loaded, degrades functionality rather than breaking it.

5. **A proper internal state machine per subsystem** (scheduler, watcher,
   plugin loader) with explicit states (idle / running / degraded / error)
   surfaced in the admin panel — cognitive robustness starts with the
   system accurately knowing and reporting its own state, independent of
   whether AI is involved at all.

The throughline: treat "intelligence" as layered — deterministic rules and
statistics as the reliable base layer that runs always, with the LLM as an
optional, more expensive layer invoked deliberately on top. That ordering
is what makes an automation platform actually dependable on hardware as
resource-constrained as a phone.
