package com.khan.pybox

import android.graphics.BitmapFactory
import android.os.Bundle
import android.widget.ImageView
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import java.io.File

/**
 * Generic viewer for anything the file explorer doesn't hand off to
 * DbViewerActivity: images render inline; text/JSON/CSV/log/md show as
 * selectable monospace text (JSON gets pretty-printed); anything else
 * (including .enc encrypted backups, which are opaque by design) falls
 * back to a short hex preview plus file info, rather than pretending to
 * decode something it can't.
 */
class FileViewerActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_file_viewer)

        val path = intent.getStringExtra("file_path") ?: run { finish(); return }
        val file = File(path)
        findViewById<TextView>(R.id.viewer_title).text = file.name

        val ext = file.extension.lowercase()
        when {
            ext in setOf("jpg", "jpeg", "png", "gif", "webp", "bmp") -> showImage(file)
            ext in setOf("json", "txt", "log", "md", "csv", "xml", "yaml", "yml") -> showText(file, pretty = ext == "json")
            file.length() < 200_000 && looksLikeText(file) -> showText(file, pretty = false)
            else -> showHexPreview(file)
        }
    }

    private fun showImage(file: File) {
        val bmp = BitmapFactory.decodeFile(file.absolutePath)
        if (bmp == null) {
            showHexPreview(file)
            return
        }
        findViewById<ImageView>(R.id.image_content).setImageBitmap(bmp)
        findViewById<ScrollView>(R.id.image_scroll).visibility = android.view.View.VISIBLE
    }

    private fun showText(file: File, pretty: Boolean) {
        var text = try {
            file.readText().take(500_000)
        } catch (e: Exception) {
            "Couldn't read file: ${e.message}"
        }
        if (pretty && text.isNotBlank()) {
            text = try {
                prettyPrintJson(text)
            } catch (e: Exception) {
                text // fall back to raw if it's not valid JSON
            }
        }
        findViewById<TextView>(R.id.text_content).text = text
        findViewById<ScrollView>(R.id.text_scroll).visibility = android.view.View.VISIBLE
    }

    private fun showHexPreview(file: File) {
        val bytes = file.inputStream().use { it.readNBytes(512) }
        val hex = bytes.joinToString(" ") { "%02x".format(it) }
        val info = "File: ${file.name}\nSize: ${file.length()} bytes\n" +
            "(binary or encrypted content - showing first ${bytes.size} bytes as hex)\n\n$hex"
        findViewById<TextView>(R.id.text_content).text = info
        findViewById<ScrollView>(R.id.text_scroll).visibility = android.view.View.VISIBLE
    }

    private fun looksLikeText(file: File): Boolean {
        return try {
            val sample = file.inputStream().use { it.readNBytes(512) }
            sample.all { it.toInt() in 9..126 || it.toInt() < 0 }
        } catch (e: Exception) {
            false
        }
    }

    private fun prettyPrintJson(raw: String): String {
        val trimmed = raw.trim()
        return if (trimmed.startsWith("[")) {
            org.json.JSONArray(trimmed).toString(2)
        } else {
            org.json.JSONObject(trimmed).toString(2)
        }
    }
}
