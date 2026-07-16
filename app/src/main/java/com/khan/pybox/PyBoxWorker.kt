package com.khan.pybox

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.Constraints
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit

/**
 * Periodic background work that survives Doze/app-kill better than the
 * in-process polling thread scheduler.py runs (which needs the Python
 * process alive - fine while the app is open, less reliable once Android
 * has frozen it). This complements scheduler.py rather than replacing it:
 *
 *   1. Pings the backend - if it's down, nothing else here can run anyway,
 *      so MainActivity's own watchdog (already handles in-app restarts)
 *      is left to do that; this just logs the miss.
 *   2. If usage-stats permission is granted, collects and reports the
 *      last day of app usage to /usage/report - this is what keeps
 *      screen-time data current even when you haven't opened the app.
 *
 * Registered as periodic work in MainActivity.onCreate() via
 * PyBoxWorker.schedule(context). WorkManager handles retry/backoff and
 * respects battery/network constraints on its own.
 */
class PyBoxWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    companion object {
        private const val WORK_NAME = "pybox_periodic_sync"
        private const val BACKEND_URL = "http://127.0.0.1:5000"

        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.NOT_REQUIRED) // backend is loopback-only
                .build()
            val request = PeriodicWorkRequestBuilder<PyBoxWorker>(1, TimeUnit.HOURS)
                .setConstraints(constraints)
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME, ExistingPeriodicWorkPolicy.KEEP, request
            )
        }

        fun cancel(context: Context) {
            WorkManager.getInstance(context).cancelUniqueWork(WORK_NAME)
        }
    }

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        try {
            if (!pingBackend()) return@withContext Result.retry()

            if (UsageStatsHelper.hasPermission(applicationContext)) {
                reportUsage()
            }
            Result.success()
        } catch (e: Exception) {
            Result.retry()
        }
    }

    private fun pingBackend(): Boolean {
        return try {
            val conn = URL(BACKEND_URL + "/").openConnection() as HttpURLConnection
            conn.connectTimeout = 2000
            conn.readTimeout = 2000
            val ok = conn.responseCode in 200..499
            conn.disconnect()
            ok
        } catch (e: Exception) {
            false
        }
    }

    private fun reportUsage() {
        val usages = UsageStatsHelper.getUsageToday(applicationContext, days = 1)
        if (usages.isEmpty()) return

        val dayFmt = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US)
        val today = dayFmt.format(java.util.Date())

        val entries = JSONArray()
        usages.forEach { u ->
            entries.put(org.json.JSONObject().apply {
                put("package_name", u.packageName)
                put("app_label", u.label)
                put("day", today)
                put("foreground_ms", u.totalTimeMs)
            })
        }
        val body = org.json.JSONObject().apply { put("entries", entries) }

        val conn = URL(BACKEND_URL + "/usage/report").openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json")
        conn.setRequestProperty("X-PyBox-Token", authToken())
        conn.doOutput = true
        conn.outputStream.use { it.write(body.toString().toByteArray()) }
        conn.responseCode // trigger the request
        conn.disconnect()
    }

    private fun authToken(): String {
        val tokenFile = File(applicationContext.filesDir, "auth_token.txt")
        return if (tokenFile.exists()) tokenFile.readText().trim() else ""
    }
}
