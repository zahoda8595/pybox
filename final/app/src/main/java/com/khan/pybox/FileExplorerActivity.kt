package com.khan.pybox

import android.app.AlertDialog
import android.app.ListActivity
import android.content.Intent
import android.os.Bundle
import android.os.Environment
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import java.io.File
import java.text.SimpleDateFormat
import java.util.Locale

/**
 * Browses this app's own local storage - the Chaquopy filesDir (where
 * contacts.db, usage_stats.db, encrypted backups, scraped/imported files
 * live) and the PyBox external folder. Tapping a file opens the right
 * viewer: DbViewerActivity for .db/.sqlite files, FileViewerActivity for
 * everything else (images render inline, text/JSON/CSV/logs show as
 * text, anything else falls back to a hex/info dump).
 *
 * Navigation: a small toolbar (Up / Home / Refresh / New Folder) sits
 * above the path bar, and the Android back button goes up one folder
 * level at a time instead of closing the screen immediately - it only
 * finishes the Activity once you're already at a root.
 *
 * This only ever browses folders PyBox itself created and writes to -
 * it does not have (and does not request) access to other apps' private
 * storage.
 */
class FileExplorerActivity : ListActivity() {

    private lateinit var currentDir: File
    private val rootDirs = mutableListOf<File>()
    private val entries = mutableListOf<File>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_file_explorer)

        rootDirs.add(filesDir)
        val pyboxExternal = File(Environment.getExternalStorageDirectory(), "PyBox")
        if (pyboxExternal.exists() || pyboxExternal.mkdirs()) rootDirs.add(pyboxExternal)

        val startPath = intent.getStringExtra("start_path")
        currentDir = if (startPath != null) File(startPath) else filesDir

        findViewById<Button>(R.id.fe_up_button).setOnClickListener { goUp() }
        findViewById<Button>(R.id.fe_home_button).setOnClickListener {
            currentDir = filesDir
            render()
        }
        findViewById<Button>(R.id.fe_refresh_button).setOnClickListener { render() }
        findViewById<Button>(R.id.fe_new_folder_button).setOnClickListener { promptNewFolder() }

        render()
    }

    /** Back button goes up one folder level; only closes the screen once
     * we're already sitting at a root listing (matches how a normal
     * Android file-manager app behaves, instead of exiting immediately). */
    override fun onBackPressed() {
        if (currentDir.parentFile != null && rootDirs.none { it == currentDir }) {
            goUp()
        } else {
            super.onBackPressed()
        }
    }

    private fun goUp() {
        if (currentDir.parentFile != null && rootDirs.none { it == currentDir }) {
            currentDir = currentDir.parentFile ?: filesDir
            render()
        } else {
            Toast.makeText(this, "Already at the top", Toast.LENGTH_SHORT).show()
        }
    }

    private fun promptNewFolder() {
        val input = EditText(this)
        input.hint = "folder name"
        AlertDialog.Builder(this)
            .setTitle("New folder")
            .setView(input)
            .setPositiveButton("Create") { _, _ ->
                val name = input.text.toString().trim()
                if (name.isNotEmpty() && !name.contains("/") && !name.contains("..")) {
                    val ok = File(currentDir, name).mkdirs()
                    if (ok) render() else Toast.makeText(this, "Could not create folder", Toast.LENGTH_SHORT).show()
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    private fun render() {
        findViewById<TextView>(R.id.path_bar).text = currentDir.absolutePath

        entries.clear()
        // At a "root" listing, show all known roots as jump-in points.
        if (currentDir == filesDir && rootDirs.size > 1) {
            entries.addAll(rootDirs.filter { it != filesDir })
        }
        currentDir.listFiles()?.sortedWith(compareBy({ !it.isDirectory }, { it.name.lowercase() }))
            ?.let { entries.addAll(it) }

        val labels = mutableListOf<String>()
        val hasUp = currentDir.parentFile != null && rootDirs.none { it == currentDir }
        if (hasUp) {
            labels.add(".. (up)")
        }
        entries.forEach { f ->
            val marker = if (f.isDirectory) "📁 " else iconFor(f)
            val size = if (f.isFile) " (${humanSize(f.length())})" else ""
            labels.add("$marker${f.name}$size")
        }

        if (labels.isEmpty()) labels.add("(empty folder)")

        listAdapter = ArrayAdapter(this, android.R.layout.simple_list_item_1, labels)

        listView.setOnItemClickListener { _, _, position, _ ->
            val upOffset = if (hasUp) 1 else 0
            if (upOffset == 1 && position == 0) {
                goUp()
                return@setOnItemClickListener
            }
            val entryIndex = position - upOffset
            if (entryIndex !in entries.indices) return@setOnItemClickListener
            val target = entries[entryIndex]
            if (target.isDirectory) {
                currentDir = target
                render()
            } else {
                openFile(target)
            }
        }

        listView.setOnItemLongClickListener { _, _, position, _ ->
            val upOffset = if (hasUp) 1 else 0
            val entryIndex = position - upOffset
            if (entryIndex !in entries.indices) return@setOnItemLongClickListener true
            showFileActions(entries[entryIndex])
            true
        }
    }

    private fun showFileActions(target: File) {
        AlertDialog.Builder(this)
            .setTitle(target.name)
            .setItems(arrayOf("Open", "Delete")) { _, which ->
                when (which) {
                    0 -> if (target.isDirectory) { currentDir = target; render() } else openFile(target)
                    1 -> confirmDelete(target)
                }
            }
            .show()
    }

    private fun confirmDelete(target: File) {
        AlertDialog.Builder(this)
            .setTitle("Delete ${target.name}?")
            .setMessage(if (target.isDirectory) "This deletes the folder and everything inside it." else "This cannot be undone.")
            .setPositiveButton("Delete") { _, _ ->
                target.deleteRecursively()
                render()
            }
            .setNegativeButton("Cancel", null)
            .show()
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
        "py" -> "🐍 "
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
