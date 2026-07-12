package com.khan.pybox

import android.Manifest
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.View
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONArray
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import android.webkit.JavascriptInterface

/**
 * Exposes the backend's per-install auth token (written by auth.py to
 * FILES_DIR/auth_token.txt) to page JS as window.PyBoxAuth.getToken(),
 * so fetch() calls to the automation endpoints and other protected routes
 * can attach it without you hand-managing it in every page. Read-only,
 * confined to this app's own WebView.
 */
class PyBoxJsBridge(private val filesDir: File) {
    @JavascriptInterface
    fun getToken(): String {
        val tokenFile = File(filesDir, "auth_token.txt")
        return if (tokenFile.exists()) tokenFile.readText().trim() else ""
    }
}

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var loadingOverlay: LinearLayout
    private lateinit var errorOverlay: LinearLayout
    private lateinit var errorText: TextView

    private val serverUrl = "http://127.0.0.1:5000/"
    private val handler = Handler(Looper.getMainLooper())

    // --- initial startup polling ---
    private var startupAttempts = 0
    private val maxStartupAttempts = 20   // ~10 seconds
    private val pollDelayMs = 500L

    // --- ongoing watchdog / auto-recovery ---
    private var watchdogRunning = false
    private var backendIsUp = false
    private var restartAttempts = 0
    private val maxRestartAttempts = 3
    private val watchdogIntervalMs = 15_000L

    // --- error tracking for notifications ---
    private var lastKnownErrorCount = 0

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* no-op either way */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        NotificationHelper.ensureChannel(this)
        requestNotificationPermissionIfNeeded()
        requestStorageAccessIfNeeded()

        webView = findViewById(R.id.webview)
        loadingOverlay = findViewById(R.id.loading_overlay)
        errorOverlay = findViewById(R.id.error_overlay)
        errorText = findViewById(R.id.error_text)

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.webViewClient = object : WebViewClient() {
            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                super.onReceivedError(view, request, error)
                if (request?.isForMainFrame == true) {
                    showError("The app's page failed to load. This usually means your Python code raised an error on startup. Check the log for details.")
                }
            }
        }

        findViewById<Button>(R.id.retry_button).setOnClickListener {
            errorOverlay.visibility = View.GONE
            loadingOverlay.visibility = View.VISIBLE
            startupAttempts = 0
            restartAttempts = 0
            pollForStartup()
        }

        findViewById<Button>(R.id.view_log_button).setOnClickListener { showLog() }
        findViewById<Button>(R.id.settings_button).setOnClickListener { showSettingsMenu() }

        startBackend()
        pollForStartup()
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED
            ) {
                notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
    }

    /**
     * MANAGE_EXTERNAL_STORAGE is a "special" permission — it can't be granted
     * via the normal runtime dialog, only by the user flipping it on in
     * Settings. This just opens that screen; there's no silent way around it,
     * by design, and that's not something to try to route around.
     */
    private fun requestStorageAccessIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && !Environment.isExternalStorageManager()) {
            try {
                val intent = Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION)
                intent.data = Uri.parse("package:$packageName")
                startActivity(intent)
            } catch (e: Exception) {
                startActivity(Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION))
            }
        }
    }

    private fun startLlamaEngine() {
        val modelDir = File(Environment.getExternalStorageDirectory(), "PyBox/models")
        val modelFile = File(modelDir, "model.gguf")
        if (!modelFile.exists()) {
            AlertDialog.Builder(this)
                .setTitle("No model found")
                .setMessage("Put a .gguf model file at:\n\n${modelFile.absolutePath}\n\nthen try again.")
                .setPositiveButton("OK", null)
                .show()
            return
        }
        val intent = Intent(this, LlamaEngineService::class.java).apply {
            action = LlamaEngineService.ACTION_START
            putExtra(LlamaEngineService.EXTRA_MODEL_PATH, modelFile.absolutePath)
        }
        ContextCompat.startForegroundService(this, intent)
    }

    private fun stopLlamaEngine() {
        val intent = Intent(this, LlamaEngineService::class.java).apply {
            action = LlamaEngineService.ACTION_STOP
        }
        startService(intent)
    }

    /** Starts the embedded Python backend on a background thread. */
    private fun startBackend() {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
        }
        Thread {
            try {
                val py = Python.getInstance()
                val backend = py.getModule("backend_app")
                val pluginsDir = File(Environment.getExternalStorageDirectory(), "PyBox/plugins")
                pluginsDir.mkdirs()
                backend.callAttr("start_server", filesDir.absolutePath, pluginsDir.absolutePath)
                // app.run() returns if the server is stopped/crashes - the
                // watchdog notices this via failed pings, not here.
            } catch (e: Exception) {
                handler.post {
                    showError("The Python backend crashed on startup:\n\n${e.message}")
                }
            }
        }.start()
    }

    /** Initial "is the server up yet" polling loop, shown as the loading screen. */
    private fun pollForStartup() {
        pingServer { ready ->
            if (ready) {
                backendIsUp = true
                loadingOverlay.visibility = View.GONE
                webView.addJavascriptInterface(PyBoxJsBridge(filesDir), "PyBoxAuth")
                webView.loadUrl(serverUrl)
                startWatchdog()
            } else if (startupAttempts < maxStartupAttempts) {
                startupAttempts++
                handler.postDelayed({ pollForStartup() }, pollDelayMs)
            } else {
                showError("The local backend didn't respond after ${maxStartupAttempts * pollDelayMs / 1000}s. It may have failed to start - check the log.")
            }
        }
    }

    /** Background health check loop. Auto-restarts the backend if it goes down. */
    private fun startWatchdog() {
        if (watchdogRunning) return
        watchdogRunning = true
        handler.postDelayed(watchdogTick, watchdogIntervalMs)
    }

    private val watchdogTick: Runnable = object : Runnable {
        override fun run() {
            pingServer { ready ->
                if (ready) {
                    if (!backendIsUp && restartAttempts > 0) {
                        // it was down, now it's back
                        NotificationHelper.notifyAutoRecovered(this@MainActivity)
                        restartAttempts = 0
                    }
                    backendIsUp = true
                    checkForNewErrors()
                } else {
                    backendIsUp = false
                    if (restartAttempts < maxRestartAttempts) {
                        restartAttempts++
                        NotificationHelper.notifyBackendDown(this@MainActivity, restartAttempts, maxRestartAttempts)
                        startBackend()
                    } else {
                        NotificationHelper.notifyRecoveryFailed(this@MainActivity)
                        showError("The backend stopped responding and automatic restart didn't fix it after $maxRestartAttempts tries. Check the log, then tap Retry.")
                        watchdogRunning = false
                        return@pingServer
                    }
                }
                handler.postDelayed(this, watchdogIntervalMs)
            }
        }
    }

    /** Reads errors.jsonl and notifies if new entries appeared since last check. */
    private fun checkForNewErrors() {
        Thread {
            val errors = readErrors()
            val count = errors.length()
            if (lastKnownErrorCount == 0) {
                lastKnownErrorCount = count // don't notify on first read after launch
            } else if (count > lastKnownErrorCount) {
                val newOnes = count - lastKnownErrorCount
                lastKnownErrorCount = count
                handler.post { NotificationHelper.notifyNewErrors(this, newOnes) }
            }
        }.start()
    }

    private fun readErrors(): JSONArray {
        val file = File(filesDir, "errors.jsonl")
        val arr = JSONArray()
        if (!file.exists()) return arr
        file.readLines().forEach { line ->
            if (line.isNotBlank()) {
                try {
                    arr.put(org.json.JSONObject(line))
                } catch (e: Exception) { /* skip malformed line */ }
            }
        }
        return arr
    }

    private fun pingServer(callback: (Boolean) -> Unit) {
        Thread {
            var ready = false
            try {
                val conn = URL(serverUrl).openConnection() as HttpURLConnection
                conn.connectTimeout = 400
                conn.readTimeout = 400
                conn.requestMethod = "GET"
                ready = conn.responseCode in 200..499
                conn.disconnect()
            } catch (e: Exception) {
                ready = false
            }
            handler.post { callback(ready) }
        }.start()
    }

    private fun showError(message: String) {
        loadingOverlay.visibility = View.GONE
        errorText.text = message
        errorOverlay.visibility = View.VISIBLE
    }

    private fun showLog() {
        val logFile = File(filesDir, "pybox.log")
        val content = if (logFile.exists()) logFile.readText().takeLast(4000)
            else "No log file yet - nothing has been logged."
        AlertDialog.Builder(this)
            .setTitle("pybox.log (last 4000 chars)")
            .setMessage(content)
            .setPositiveButton("Close", null)
            .show()
    }

    private fun showErrorHistory() {
        val errors = readErrors()
        val text = if (errors.length() == 0) {
            "No errors logged yet."
        } else {
            val sb = StringBuilder()
            val start = maxOf(0, errors.length() - 10)
            for (i in start until errors.length()) {
                val e = errors.getJSONObject(i)
                sb.append("[${e.optString("time")}] ${e.optString("route")}: ")
                sb.append("${e.optString("error_type")} - ${e.optString("message")}\n\n")
            }
            sb.toString()
        }
        AlertDialog.Builder(this)
            .setTitle("Recent errors (last 10)")
            .setMessage(text)
            .setPositiveButton("Close", null)
            .show()
    }

    private fun showSettingsMenu() {
        val status = if (backendIsUp) "Backend: running" else "Backend: down"
        AlertDialog.Builder(this)
            .setTitle("PyBox controls — $status")
            .setItems(arrayOf("Reload", "Open Admin Panel", "View Log", "View Error History", "Start LLM Engine", "Stop LLM Engine", "Reset App Data")) { _, which ->
                when (which) {
                    0 -> webView.loadUrl(serverUrl)
                    1 -> webView.loadUrl(serverUrl + "admin")
                    2 -> showLog()
                    3 -> showErrorHistory()
                    4 -> startLlamaEngine()
                    5 -> stopLlamaEngine()
                    6 -> confirmReset()
                }
            }
            .show()
    }

    /** Deletes locally stored files (DBs, logs, etc.) - asks for confirmation first. */
    private fun confirmReset() {
        AlertDialog.Builder(this)
            .setTitle("Reset app data?")
            .setMessage("This permanently deletes everything stored in this app's private storage (databases, logs, error history). This cannot be undone. Fully close and reopen the app afterward for a clean restart.")
            .setPositiveButton("Delete") { _, _ ->
                filesDir.listFiles()?.forEach { it.deleteRecursively() }
                lastKnownErrorCount = 0
                AlertDialog.Builder(this)
                    .setTitle("Data reset")
                    .setMessage("App data cleared. Please force-close PyBox from your recent apps and reopen it.")
                    .setPositiveButton("OK", null)
                    .show()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
}
