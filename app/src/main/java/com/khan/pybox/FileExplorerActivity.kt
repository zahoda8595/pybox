package com.khan.pybox

import android.app.ListActivity
import android.content.Intent
import android.os.Bundle
import android.os.Environment
import android.widget.ArrayAdapter
import android.widget.TextView
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Browses this app's own local storage - the Chaquopy filesDir (where
 * contacts.db, usage_stats.db, encrypted backups, scraped/imported files
 * live) and the PyBox external folder. Tapping a file opens the right
 * viewer: DbViewerActivity for .db/.sqlite files, FileViewerActivity for
 * everything else (images render inline, text/JSON/CSV/logs show as
 * text, anything else falls back to a hex/info dump).
 *
 * This only ever browses folders PyBox itself created and writes to -
 * it does not have (and does not request) access to other apps' private
 * storage.
 */
class FileExplorerActivity : ListActivity() {

    private lateinit var currentDir: File
    private val rootDirs = mutableListOf<File>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_file_explorer)

        rootDirs.add(filesDir)
        val pyboxExternal = File(Environment.getExternalStorageDirectory(), "PyBox")
        if (pyboxExternal.exists() || pyboxExternal.mkdirs()) rootDirs.add(pyboxExternal)

        val startPath = intent.getStringExtra("start_path")
        currentDir = if (startPath != null) File(startPath) else filesDir
        render()
    }

    private fun render() {
        findViewById<TextView>(R.id.path_bar).text = currentDir.absolutePath

        val entries = mutableListOf<File>()
        // At a "root" listing, show all known roots as jump-in points.
        if (currentDir == filesDir && rootDirs.size > 1) {
            entries.addAll(rootDirs.filter { it != filesDir })
        }
        currentDir.listFiles()?.sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))
            ?.let { entries.addAll(it) }

        val labels = mutableListOf<String>()
        if (currentDir.parentFile != null && rootDirs.none { it == currentDir }) {
            labels.add(".. (up)")
        }
        val dateFmt = SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.US)
        entries.forEach { f ->
            val marker = if (f.isDirectory) "📁 " else iconFor(f)
            val size = if (f.isFile) " (${humanSize(f.length())})" else ""
            labels.add("$marker${f.name}$size")
        }

        listAdapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, labels)

        listView.setOnItemClickListener { _, _, position, _ ->
            val upOffset = if (currentDir.parentFile != null && rootDirs.none { it == currentDir }) 1 else 0
            if (upOffset == 1 && position == 0) {
                currentDir = currentDir.parentFile ?: filesDir
                render()
                return@setOnItemClickListener
            }
            val entryIndex = position - upOffset
            val target = entries[entryIndex]
            if (target.isDirectory) {
                currentDir = target
                render()
            } else {
                openFile(target)
            }
        }
    }

    private fun openFile(file: File) {
        val ext = file.extension.lowercase()
        if (ext == "db" || ext == "sqlite" || ext == "sqlite3" || looksLikeSqlite(file)) {
            startActivity(Intent(this, DbViewerActivity::class.java).apply {
                putExtra("db_path", file.absolutePath)
            })
        } else {
            startActivity(Intent(this, FileViewerActivity::class.java).apply {
                putExtra("file_path", file.absolutePath)
            })
        }
    }

    /** SQLite files start with a fixed 16-byte magic header regardless of
     * extension - checking it catches renamed/extensionless DB files too. */
    private fun looksLikeSqlite(file: File): Boolean {
        return try {
            file.inputStream().use { stream ->
                val header = ByteArray(16)
                val read = stream.read(header)
                read == 16 && String(header, Charsets.US_ASCII).startsWith("SQLite format 3")
            }
        } catch (e: Exception) {
            false
        }
    }

    private fun iconFor(f: File): String = when (f.extension.lowercase()) {
        "jpg", "jpeg", "png", "gif", "webp", "bmp" -> "🖼️ "
        "db", "sqlite", "sqlite3" -> "🗄️ "
        "json" -> "🔧 "
        "csv" -> "📊 "
        "txt", "log", "md" -> "📄 "
        "enc" -> "🔒 "
        else -> "📎 "
    }

    private fun humanSize(bytes: Long): String {
        if (bytes < 1024) return "${bytes}B"
        val kb = bytes / 1024.0
        if (kb < 1024) return "%.1fKB".format(kb)
        val mb = kb / 1024.0
        return "%.1fMB".format(mb)
    }
}
