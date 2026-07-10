package com.khan.pybox

import android.app.*
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Environment
import android.os.IBinder
import android.util.Log
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader

/**
 * Runs the precompiled llama.cpp server binary (libllama_server.so, built by
 * app/src/main/cpp/CMakeLists.txt) as a background process, bound to
 * 127.0.0.1:8081, loopback only. PyBox's Python/Flask backend
 * (backend_app.py) talks to it over that loopback socket — see the
 * /llm/* routes there.
 *
 * This is a genuine headless background process: it keeps running whether
 * or not MainActivity is on screen. Android requires a visible notification
 * for that (foreground service rule) — this is intentional on Android's
 * part, not something this app tries to hide, because a process silently
 * running in the background with no user-visible trace is exactly the
 * pattern malware uses.
 *
 * Model file: drop a .gguf file anywhere readable (given
 * MANAGE_EXTERNAL_STORAGE) and point MODEL_PATH at it, or default to
 * /sdcard/PyBox/models/model.gguf.
 */
class LlamaEngineService : Service() {

    companion object {
        private const val TAG = "LlamaEngineService"
        private const val CHANNEL_ID = "pybox_llama_engine"
        private const val NOTIF_ID = 42
        const val PORT = 8081
        const val ACTION_START = "com.khan.pybox.action.START_ENGINE"
        const val ACTION_STOP = "com.khan.pybox.action.STOP_ENGINE"
        const val EXTRA_MODEL_PATH = "model_path"
    }

    private var process: Process? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopEngine()
                stopSelf()
                return START_NOT_STICKY
            }
            else -> {
                val modelPath = intent?.getStringExtra(EXTRA_MODEL_PATH)
                    ?: defaultModelPath()
                startForeground(NOTIF_ID, buildNotification("Starting…"))
                startEngine(modelPath)
            }
        }
        return START_STICKY
    }

    private fun defaultModelPath(): String =
        File(Environment.getExternalStorageDirectory(), "PyBox/models/model.gguf").absolutePath

    private fun startEngine(modelPath: String) {
        if (process != null) {
            Log.i(TAG, "Engine already running")
            return
        }
        val modelFile = File(modelPath)
        if (!modelFile.exists()) {
            updateNotification("No model found at $modelPath")
            Log.e(TAG, "Model file missing: $modelPath")
            stopSelf()
            return
        }

        // Binaries built into jniLibs land in applicationInfo.nativeLibraryDir
        // and are the only files on the filesystem this app is permitted to
        // execute directly (W^X enforcement on Android 10+).
        val binaryPath = File(applicationInfo.nativeLibraryDir, "libllama_server.so").absolutePath
        val threads = Runtime.getRuntime().availableProcessors().coerceAtLeast(1)

        val cmd = listOf(
            binaryPath,
            "--model", modelPath,
            "--host", "127.0.0.1",
            "--port", PORT.toString(),
            "--threads", threads.toString(),
            "--ctx-size", "4096",
            "--no-webui"
        )

        try {
            val pb = ProcessBuilder(cmd)
                .redirectErrorStream(true)
                .directory(filesDir)
            process = pb.start()
            updateNotification("Running · port $PORT · $threads threads")

            Thread {
                val reader = BufferedReader(InputStreamReader(process!!.inputStream))
                var line: String?
                while (reader.readLine().also { line = it } != null) {
                    Log.d(TAG, "[llama-server] $line")
                }
                Log.i(TAG, "llama-server process ended")
                updateNotification("Stopped")
            }.start()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start llama-server", e)
            updateNotification("Failed to start: ${e.message}")
        }
    }

    private fun stopEngine() {
        process?.destroy()
        process = null
    }

    override fun onDestroy() {
        stopEngine()
        super.onDestroy()
    }

    private fun buildNotification(status: String): Notification {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID, "PyBox Local LLM Engine", NotificationManager.IMPORTANCE_LOW
            )
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
        return Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("PyBox local LLM engine")
            .setContentText(status)
            .setSmallIcon(android.R.drawable.ic_menu_manage)
            .setOngoing(true)
            .build()
    }

    private fun updateNotification(status: String) {
        val nm = getSystemService(NotificationManager::class.java)
        nm.notify(NOTIF_ID, buildNotification(status))
    }

    override fun onCreate() {
        super.onCreate()
    }
}
