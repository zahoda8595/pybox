package com.khan.pybox

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

/**
 * Central place for every notification PyBox sends. Keeps the tone
 * calm and informative - it tells you what happened and whether the
 * app already handled it, so a notification isn't automatically bad
 * news.
 */
object NotificationHelper {

    private const val CHANNEL_ID = "pybox_status"
    private var nextId = 1000

    fun ensureChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "PyBox backend status",
                NotificationManager.IMPORTANCE_DEFAULT
            ).apply {
                description = "Backend health, auto-recovery, and error alerts"
            }
            val manager = context.getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(channel)
        }
    }

    private fun send(context: Context, title: String, text: String) {
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setAutoCancel(true)
            .build()
        try {
            NotificationManagerCompat.from(context).notify(nextId++, notification)
        } catch (e: SecurityException) {
            // Notification permission not granted - fail quietly, the
            // in-app error/log views still work without it.
        }
    }

    fun notifyBackendDown(context: Context, attempt: Int, maxAttempts: Int) {
        send(
            context,
            "PyBox backend stopped responding",
            "Attempting automatic restart ($attempt/$maxAttempts)…"
        )
    }

    fun notifyAutoRecovered(context: Context) {
        send(
            context,
            "PyBox backend recovered",
            "The backend stopped responding and has been restarted automatically. No action needed."
        )
    }

    fun notifyRecoveryFailed(context: Context) {
        send(
            context,
            "PyBox backend needs your attention",
            "Automatic restart attempts were unsuccessful. Open the app and tap Retry, or check the log."
        )
    }

    fun notifyNewErrors(context: Context, count: Int) {
        send(
            context,
            "PyBox logged $count new error(s)",
            "One or more routes hit an error. The app is still running - tap View Log in settings for details."
        )
    }
}
