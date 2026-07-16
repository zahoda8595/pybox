package com.khan.pybox

import android.app.AlertDialog
import android.content.Intent
import android.os.Bundle
import android.view.KeyEvent
import android.view.inputmethod.EditorInfo
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.util.regex.Pattern

/**
 * PyBox's own in-app browser. Two ways in:
 *   1. Launched normally from the app, type a URL, browse.
 *   2. Launched via Android's Share sheet from Chrome (or any app) - a
 *      shared URL opens here directly, ready to extract.
 *
 * "Extract data from this page" pulls the CURRENT RENDERED DOM (after
 * JavaScript has run), not a raw HTTP fetch - this is what makes it a
 * real complement to scraper.py's plain requests.get, which can't see
 * JS-rendered content at all.
 *
 * This never reads Chrome's own browsing state. It only ever sees pages
 * YOU load here, or a URL YOU explicitly shared into it.
 */
class BrowserActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var urlBar: EditText
    private val backendUrl = "http://127.0.0.1:5000"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_browser)

        webView = findViewById(R.id.browser_webview)
        urlBar = findViewById(R.id.url_bar)
        val goButton = findViewById<Button>(R.id.go_button)
        val extractButton = findViewById<Button>(R.id.extract_button)
        val rulesButton = findViewById<Button>(R.id.rules_button)

        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                urlBar.setText(url ?: "")
            }
        }

        goButton.setOnClickListener { navigate(urlBar.text.toString()) }
        urlBar.setOnEditorActionListener { _, actionId, event ->
            if (actionId == EditorInfo.IME_ACTION_GO ||
                (event?.keyCode == KeyEvent.KEYCODE_ENTER)) {
                navigate(urlBar.text.toString())
                true
            } else false
        }
        extractButton.setOnClickListener { extractCurrentPage() }
        rulesButton.setOnClickListener { showRulesDialog() }

        handleIncomingIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIncomingIntent(intent)
    }

    /** Handles both a normal launch and a Chrome/any-app Share into this Activity. */
    private fun handleIncomingIntent(intent: Intent) {
        if (intent.action == Intent.ACTION_SEND && intent.type == "text/plain") {
            val sharedText = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return
            val url = extractUrl(sharedText)
            if (url != null) {
                Toast.makeText(this, "Opened shared page - tap Extract when ready", Toast.LENGTH_SHORT).show()
                navigate(url)
            }
        }
    }

    /** Chrome's share text is often "Page Title\nhttps://..." - pull just the URL out. */
    private fun extractUrl(text: String): String? {
        val matcher = Pattern.compile("https?://\\S+").matcher(text)
        return if (matcher.find()) matcher.group() else null
    }

    private fun navigate(input: String) {
        var url = input.trim()
        if (url.isEmpty()) return
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "https://$url"
        }
        webView.loadUrl(url)
    }

    private fun extractCurrentPage() {
        val currentUrl = webView.url ?: return
        webView.evaluateJavascript("document.documentElement.outerHTML") { html ->
            // evaluateJavascript returns a JSON-encoded string - decode it.
            val decoded = JSONObject.quote("").let {
                try {
                    org.json.JSONTokener(html).nextValue() as String
                } catch (e: Exception) {
                    html
                }
            }
            Thread {
                try {
                    val body = JSONObject().apply {
                        put("url", currentUrl)
                        put("html", decoded)
                    }
                    val response = postToBackend("/browser/extract", body.toString())
                    runOnUiThread { showResultDialog(response) }
                } catch (e: Exception) {
                    runOnUiThread {
                        Toast.makeText(this, "Extraction failed: ${e.message}", Toast.LENGTH_LONG).show()
                    }
                }
            }.start()
        }
    }

    private fun showRulesDialog() {
        val currentUrl = webView.url
        val domain = currentUrl?.let { Uri_getHost(it) } ?: ""
        Thread {
            try {
                val response = getFromBackend("/browser/rules?domain=$domain")
                runOnUiThread { showResultDialog(response, title = "Extraction rules for $domain") }
            } catch (e: Exception) {
                runOnUiThread {
                    Toast.makeText(this, "Could not load rules: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
        }.start()
    }

    private fun Uri_getHost(url: String): String {
        return try { URL(url).host } catch (e: Exception) { "" }
    }

    private fun showResultDialog(json: String, title: String = "Extracted data") {
        AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage(json)
            .setPositiveButton("Close", null)
            .show()
    }

    private fun authToken(): String {
        val tokenFile = File(filesDir, "auth_token.txt")
        return if (tokenFile.exists()) tokenFile.readText().trim() else ""
    }

    private fun postToBackend(path: String, jsonBody: String): String {
        val conn = URL(backendUrl + path).openConnection() as HttpURLConnection
        conn.requestMethod = "POST"
        conn.setRequestProperty("Content-Type", "application/json")
        conn.setRequestProperty("X-PyBox-Token", authToken())
        conn.doOutput = true
        OutputStreamWriter(conn.outputStream).use { it.write(jsonBody) }
        return readResponse(conn)
    }

    private fun getFromBackend(path: String): String {
        val conn = URL(backendUrl + path).openConnection() as HttpURLConnection
        conn.requestMethod = "GET"
        conn.setRequestProperty("X-PyBox-Token", authToken())
        return readResponse(conn)
    }

    private fun readResponse(conn: HttpURLConnection): String {
        val stream = if (conn.responseCode in 200..299) conn.inputStream else conn.errorStream
        val reader = BufferedReader(InputStreamReader(stream))
        val text = reader.readText()
        reader.close()
        return try {
            JSONObject(text).toString(2)
        } catch (e: Exception) {
            text
        }
    }
}
