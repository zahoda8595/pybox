package com.khan.pybox

import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.graphics.BitmapFactory
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.BaseAdapter
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import android.widget.Button
import android.widget.ListView
import android.widget.AdapterView

/**
 * Read-only browser for any SQLite file in this app's storage - built-in
 * DBs (contacts.db, usage_stats.db) as well as any .db file you drop into
 * the PyBox folder or restore from an encrypted backup. Lists tables,
 * paginates rows, and renders BLOB columns as either an inline thumbnail
 * (if it decodes as an image) or a byte count.
 *
 * Opened read-only (SQLiteDatabase.OPEN_READONLY) so browsing a live DB
 * can never corrupt it or interfere with the backend using it.
 */
class DbViewerActivity : AppCompatActivity() {

    private lateinit var db: SQLiteDatabase
    private lateinit var tableSpinner: Spinner
    private lateinit var rowsList: ListView
    private lateinit var rowCountLabel: TextView
    private var currentTable: String? = null
    private var currentOffset = 0
    private val pageSize = 50
    private var currentColumns: List<String> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_db_viewer)

        val path = intent.getStringExtra("db_path") ?: run { finish(); return }
        try {
            db = SQLiteDatabase.openDatabase(path, null, SQLiteDatabase.OPEN_READONLY)
        } catch (e: Exception) {
            Toast.makeText(this, "Couldn't open DB: ${e.message}", Toast.LENGTH_LONG).show()
            finish()
            return
        }

        findViewById<TextView>(R.id.db_title).text = path.substringAfterLast("/")
        tableSpinner = findViewById(R.id.table_spinner)
        rowsList = findViewById(R.id.rows_list)
        rowCountLabel = findViewById(R.id.row_count_label)

        val tables = listTables()
        if (tables.isEmpty()) {
            rowCountLabel.text = "No tables found."
            return
        }
        tableSpinner.adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, tables)
        tableSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                currentTable = tables[position]
                currentOffset = 0
                loadPage()
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        findViewById<Button>(R.id.prev_page_btn).setOnClickListener {
            if (currentOffset >= pageSize) {
                currentOffset -= pageSize
                loadPage()
            }
        }
        findViewById<Button>(R.id.next_page_btn).setOnClickListener {
            currentOffset += pageSize
            loadPage()
        }
    }

    private fun listTables(): List<String> {
        val result = mutableListOf<String>()
        db.rawQuery("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'", null).use { c ->
            while (c.moveToNext()) result.add(c.getString(0))
        }
        return result
    }

    private fun loadPage() {
        val table = currentTable ?: return
        val total = db.rawQuery("SELECT COUNT(*) FROM \"$table\"", null).use { c ->
            c.moveToFirst(); c.getInt(0)
        }
        rowCountLabel.text = "Rows ${currentOffset + 1}-${minOf(currentOffset + pageSize, total)} of $total"

        val rows = mutableListOf<Map<String, Any?>>()
        db.rawQuery("SELECT * FROM \"$table\" LIMIT $pageSize OFFSET $currentOffset", null).use { c ->
            currentColumns = c.columnNames.toList()
            while (c.moveToNext()) {
                val row = mutableMapOf<String, Any?>()
                for (i in currentColumns.indices) {
                    row[currentColumns[i]] = readCell(c, i)
                }
                rows.add(row)
            }
        }
        rowsList.adapter = RowAdapter(rows, currentColumns)
    }

    /** Reads a cell, preferring text but flagging BLOBs so the adapter can
     * try to render them as an image thumbnail rather than raw bytes. */
    private fun readCell(c: Cursor, index: Int): Any? {
        return when (c.getType(index)) {
            Cursor.FIELD_TYPE_BLOB -> c.getBlob(index)
            Cursor.FIELD_TYPE_NULL -> null
            else -> c.getString(index)
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        if (::db.isInitialized && db.isOpen) db.close()
    }

    private inner class RowAdapter(
        private val rows: List<Map<String, Any?>>,
        private val columns: List<String>
    ) : BaseAdapter() {
        override fun getCount() = rows.size
        override fun getItem(position: Int) = rows[position]
        override fun getItemId(position: Int) = position.toLong()

        override fun getView(position: Int, convertView: View?, parent: ViewGroup?): View {
            val row = rows[position]
            val container = android.widget.LinearLayout(this@DbViewerActivity).apply {
                orientation = android.widget.LinearLayout.HORIZONTAL
                setPadding(8, 8, 8, 8)
            }
            for (col in columns) {
                val value = row[col]
                if (value is ByteArray) {
                    val bmp = try { BitmapFactory.decodeByteArray(value, 0, value.size) } catch (e: Exception) { null }
                    if (bmp != null) {
                        container.addView(android.widget.ImageView(this@DbViewerActivity).apply {
                            setImageBitmap(bmp)
                            layoutParams = android.widget.LinearLayout.LayoutParams(120, 120)
                        })
                    } else {
                        container.addView(cell("[blob ${value.size}B]"))
                    }
                } else {
                    container.addView(cell(value?.toString() ?: "NULL"))
                }
            }
            return container
        }

        private fun cell(text: String) = TextView(this@DbViewerActivity).apply {
            this.text = text.take(200)
            setPadding(12, 4, 12, 4)
            minWidth = 180
            textSize = 12f
            setTextColor(android.graphics.Color.parseColor("#dddddd"))
        }
    }
}
