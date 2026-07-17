package com.khan.pybox

import android.app.AppOpsManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import java.util.Calendar

/**
 * Screen-time / app-usage stats via Android's sanctioned UsageStatsManager.
 *
 * WHY THIS IS THE RIGHT TOOL (vs. an AccessibilityService):
 *   UsageStatsManager gives foreground-time-per-app totals - aggregate
 *   numbers like "Instagram: 42 min today" - with a permission the user
 *   explicitly grants in Settings. It CANNOT see what's on screen, what
 *   was typed, or any content inside another app. An AccessibilityService
 *   can see all of that, which is exactly why it's not used here - this
 *   file gives you real screen-time data without that capability.
 *
 * PERMISSION:
 *   Requires PACKAGE_USAGE_STATS, a "special" permission like
 *   MANAGE_EXTERNAL_STORAGE - only grantable by the user manually via
 *   the Settings screen this opens, never programmatically.
 */
object UsageStatsHelper {

    fun hasPermission(context: Context): Boolean {
        val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        val mode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            appOps.unsafeCheckOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS, android.os.Process.myUid(), context.packageName
            )
        } else {
            @Suppress("DEPRECATION")
            appOps.checkOpNoThrow(
                AppOpsManager.OPSTR_GET_USAGE_STATS, android.os.Process.myUid(), context.packageName
            )
        }
        return mode == AppOpsManager.MODE_ALLOWED
    }

    fun requestPermission(context: Context) {
        context.startActivity(
            Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    data class AppUsage(val packageName: String, val label: String, val totalTimeMs: Long)

    /** Foreground time per app for the last [days] days, descending. */
    fun getUsageToday(context: Context, days: Int = 1): List<AppUsage> {
        if (!hasPermission(context)) return emptyList()

        val usm = context.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
        val end = System.currentTimeMillis()
        val cal = Calendar.getInstance()
        cal.add(Calendar.DAY_OF_YEAR, -days)
        val start = cal.timeInMillis

        val stats = usm.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, start, end)
        val pm = context.packageManager
        return stats
            .filter { it.totalTimeInForeground > 0 }
            .groupBy { it.packageName }
            .map { (pkg, entries) ->
                val total = entries.sumOf { it.totalTimeInForeground }
                val label = try {
                    pm.getApplicationLabel(pm.getApplicationInfo(pkg, PackageManager.ApplicationInfoFlags.of(0))).toString()
                } catch (e: Exception) {
                    pkg
                }
                AppUsage(pkg, label, total)
            }
            .sortedByDescending { it.totalTimeMs }
    }

    fun formatDuration(ms: Long): String {
        val totalMinutes = ms / 60000
        val h = totalMinutes / 60
        val m = totalMinutes % 60
        return if (h > 0) "${h}h ${m}m" else "${m}m"
    }
}
