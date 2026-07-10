# PyBox — local Python app shell for Android

A minimal native Android app (WebView front end) with an embedded CPython
interpreter (via [Chaquopy](https://chaquo.com/chaquopy/)) running your Flask
code as the backend — entirely on-device, entirely offline once installed.

## How it works
- `app/src/main/python/backend_app.py` — paste your Flask routes here.
- `MainActivity.kt` starts that Python backend on a background thread,
  bound to `127.0.0.1:5000` (loopback only), then points a WebView at it.
- No Android Studio needed to build — GitHub Actions builds the APK for you.

## One-time setup
1. Create a new GitHub repo (public keeps Chaquopy free — see note below).
2. From this folder:
   ```
   git init
   git add .
   git commit -m "Initial PyBox scaffold"
   git branch -M main
   git remote add origin <your-repo-url>
   git push -u origin main
   ```
3. Go to your repo's **Actions** tab — the build starts automatically.
   Takes ~3-5 minutes the first time.
4. When it finishes, open the run, scroll to **Artifacts**, download
   `pybox-debug-apk`. Unzip it to get `app-debug.apk`.
5. Transfer the APK to your phone (Drive, cable, whatever), enable
   "install unknown apps" for that source, and install it.

## Pasting your Python code
Open `app/src/main/python/backend_app.py` and paste your routes between the
marked lines. Keep using the `app` Flask object already defined — don't
create a second one. Wrap each route with `@safe_route("name")` (see the
placeholder route for the pattern) so a crash in your code gets isolated,
logged, and auto-recovered instead of taking the app down. If you need
extra pip packages, add them in `app/build.gradle` under
`chaquopy { pip { install(...) } }`.

Every time you push a change to `main`, GitHub Actions rebuilds the APK
automatically — just re-download it from Actions and reinstall.

## Control & safety features built in
- **Loading screen** while the backend starts, instead of a blank white screen.
- **Startup polling** — the app checks whether the backend is actually up
  rather than guessing a fixed delay, and shows a clear error + Retry
  button if it never comes up.
- **CI syntax check** — every push runs `py_compile` on your backend
  files before Gradle even starts, so a typo fails in ~10 seconds
  instead of after a 5-minute build.
- **Per-route crash isolation (`error_manager.py`)** — wrap any route
  with `@safe_route("name")` and a crash there logs to `errors.jsonl`
  and returns a friendly error page instead of taking the whole app
  down. A route that crashes 3 times in a row auto-disables itself for
  a minute rather than crash-looping, then quietly tries again.
- **Watchdog with auto-restart** — every 15s the app pings the backend;
  if it's gone, PyBox restarts it automatically (up to 3 tries) before
  giving up and asking you to tap Retry.
- **Notifications** — real Android notifications (needs the
  notification permission, requested on first launch) for: backend
  down + retry attempt, successful auto-recovery, new errors logged,
  and "automatic recovery failed, please check the app."
- **In-app viewers** — settings button → "View Log" (raw pybox.log) or
  "View Error History" (last 10 structured errors: route, type,
  message, time) — no computer or adb needed.
- **Safe reset** — settings button → "Reset App Data" asks for
  confirmation, then deletes everything in the app's private storage
  (databases, logs, error history). You still need to force-close and
  reopen the app afterward, since the Python process keeps running
  until the app is fully killed.

## Local LLM inference (llama.cpp, precompiled, on-device)
`app/src/main/cpp/CMakeLists.txt` cross-compiles llama.cpp's `llama-server`
for arm64-v8a as part of the normal Gradle/NDK build — it's a real native
binary baked into the APK, not a Python wrapper. `LlamaEngineService.kt`
runs it as a background process on `127.0.0.1:8081`; `backend_app.py`
proxies to it at `/llm/status` and `/llm/generate`.

**Setup:**
1. Push to `main` — CI now installs the NDK, shallow-clones llama.cpp
   fresh (pinned to whatever's current upstream), and builds it alongside
   the app. First build will take noticeably longer (~10-15 min) because
   it's compiling a C++ inference engine, not just Kotlin/Python.
2. Put a `.gguf` model file on the phone at `/sdcard/PyBox/models/model.gguf`
   (any GGUF-format quantized model — pick a size that fits the S22 Ultra's
   RAM; a 3-4B Q4 model is a safe starting point).
3. Install the APK, grant "All files access" when prompted (needed to read
   the model file and your project files generally — this is the strongest
   storage permission a sideloaded app can hold on Android 11+).
4. Settings button → **Start LLM Engine**. You'll see a persistent
   notification while it's running — Android requires that for any
   background process; it can't be suppressed, by design.
5. From your Flask routes: `POST /llm/generate` with the same JSON body
   llama.cpp's `/completion` endpoint expects (prompt, n_predict, etc).

**Honest limits:**
- I can't compile or test this in my own sandbox — no Android NDK there,
  no device to run it on. The CMake/Gradle/CI wiring is correct as far as
  I can verify by reading llama.cpp's actual current CMake options, but a
  first real build commonly needs a fix or two from reading CI logs
  (NDK/cmake version mismatches are the usual culprit). Push it, check the
  Actions tab, and send me the log if it fails partway.
- arm64-v8a only, matching your phone. Other ABIs aren't built.
- No GPU/NNAPI acceleration wired up — CPU inference via llama.cpp's
  ARM-optimized kernels (dotprod+i8mm on your chip). Fine for a few
  tokens/sec on a small quantized model; don't expect desktop-GPU speed.
- "Full admin" / unrestricted system access isn't something this (or any)
  sideloaded app gets on a non-rooted Knox device — see note below.

## On "full admin" / full system control
A regular installed app — including this one — cannot get OS-level admin
over a stock, non-rooted Samsung Knox device, no matter how it's built.
What it *can* legitimately hold, and what's wired up here:
- **Full file access** (`MANAGE_EXTERNAL_STORAGE`) — read/write anywhere
  on shared storage, not just its own sandboxed folder.
- **Real background/headless execution** — the LLM engine keeps running
  whether or not the app is on screen, via a foreground service. Android
  requires a visible notification for that; that requirement is what
  stops a background process from being invisible to you, and isn't
  something worth building around even if it were possible.
- **Full control of its own automation** — arbitrary Python/Flask logic,
  scheduled jobs, file operations, all within what you paste into
  `backend_app.py`.
Rooting the device would unlock more (true system-level control), but
that's a separate, higher-risk decision for you to make deliberately —
not something to fold into an app silently.

## Other known limits (read before pasting heavy code)
- **SQLite** works fine (it's built into Python).
- **ChromaDB** may or may not work depending on the version's dependencies
  — worth testing early, before you've built a lot of code around it.
- Anything needing a C extension without a prebuilt Android wheel will
  fail to install via Chaquopy's pip — tell me if you hit this; native
  pieces can potentially follow the same NDK-compile-in-CI path as
  llama.cpp above, but that's case-by-case.

## Chaquopy licensing
Chaquopy is free for **public/open-source** repositories. Private repos
require a paid license — check current terms at chaquo.com before you
commit to a private repo, since pricing/terms can change.
