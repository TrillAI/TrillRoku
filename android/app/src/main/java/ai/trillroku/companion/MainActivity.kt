package ai.trillroku.companion

import android.app.Activity
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

private data class VideoEntry(
    val title: String,
    val url: String,
    val thumb: String,
    val streamFormat: String,
)

class MainActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()

    private lateinit var apiBaseInput: EditText
    private lateinit var apiKeyInput: EditText
    private lateinit var sourceInput: EditText
    private lateinit var depthInput: EditText
    private lateinit var pagesInput: EditText
    private lateinit var permissionCheckBox: CheckBox
    private lateinit var scanButton: Button
    private lateinit var copyCodeButton: Button
    private lateinit var statusText: TextView
    private lateinit var scanCodeText: TextView
    private lateinit var resultsContainer: LinearLayout

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val preferences = getSharedPreferences("trillroku-companion", Context.MODE_PRIVATE)
        val scrollView = ScrollView(this)
        scrollView.setBackgroundColor(Color.rgb(245, 247, 250))
        val page = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(18), dp(18), dp(28))
        }
        scrollView.addView(page)
        setContentView(scrollView)

        page.addView(textView("TrillRoku Companion", 26f, Color.rgb(22, 38, 58), bold = true))
        page.addView(textView("Scan a permitted site and prepare a playlist for Roku.", 15f, Color.DKGRAY).withTopMargin(4))

        val disclaimer = textView(
            "Use this app only for websites and video streams you own or have explicit permission to access and play. You are responsible for rights and site terms. This app does not bypass DRM, logins, or other access controls.",
            14f,
            Color.rgb(82, 57, 0),
        ).apply {
            setPadding(dp(12), dp(12), dp(12), dp(12))
            background = GradientDrawable().apply {
                setColor(Color.rgb(255, 243, 205))
                cornerRadius = dp(8).toFloat()
            }
        }
        page.addView(disclaimer, verticalParams(top = 16))

        page.addView(
            TextView(this).apply {
                text = "No WebView is used. The app submits the URL to your TrillRoku API and renders the returned video list as native Android views."
                textSize = 13f
                setTextColor(Color.rgb(80, 94, 108))
            },
            verticalParams(top = 10),
        )

        apiBaseInput = addEditField(
            page,
            "Middleware API base URL (HTTPS)",
            "https://api.example.org",
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI,
            preferences.getString("apiBaseUrl", "") ?: "",
        )
        apiKeyInput = addEditField(
            page,
            "API key (optional)",
            "Same value as TRILLROKU_API_KEY",
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD,
            preferences.getString("apiKey", "") ?: "",
        )
        sourceInput = addEditField(
            page,
            "Website URL to scan",
            "https://example.org/videos",
            InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI,
            preferences.getString("sourceUrl", "") ?: "",
        )
        depthInput = addEditField(
            page,
            "Maximum link depth (0–5)",
            "3",
            InputType.TYPE_CLASS_NUMBER,
            preferences.getString("maxDepth", "3") ?: "3",
        )
        pagesInput = addEditField(
            page,
            "Maximum pages to scan (1–500)",
            "100",
            InputType.TYPE_CLASS_NUMBER,
            preferences.getString("maxPages", "100") ?: "100",
        )

        permissionCheckBox = CheckBox(this).apply {
            text = "I own this content or have permission to access and stream it."
            textSize = 14f
            setTextColor(Color.rgb(35, 45, 55))
        }
        page.addView(permissionCheckBox, verticalParams(top = 12))

        scanButton = Button(this).apply {
            text = "Scan Website"
            isEnabled = false
            setOnClickListener { startScan() }
        }
        page.addView(scanButton, verticalParams(top = 8))
        permissionCheckBox.setOnCheckedChangeListener { _, checked ->
            scanButton.isEnabled = checked
        }

        statusText = textView("Enter the API and website URL, acknowledge the disclaimer, then scan.", 14f, Color.rgb(55, 70, 85))
        page.addView(statusText, verticalParams(top = 12))

        scanCodeText = textView("", 18f, Color.rgb(18, 68, 112), bold = true).apply {
            visibility = View.GONE
            textIsSelectable = true
        }
        page.addView(scanCodeText, verticalParams(top = 12))
        copyCodeButton = Button(this).apply {
            text = "Copy Roku scan code"
            visibility = View.GONE
            setOnClickListener { copyScanCode() }
        }
        page.addView(copyCodeButton, verticalParams(top = 4))

        page.addView(textView("Discovered videos", 20f, Color.rgb(22, 38, 58), bold = true), verticalParams(top = 20))
        resultsContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        page.addView(resultsContainer, verticalParams(top = 8))
    }

    private fun startScan() {
        val apiBase = apiBaseInput.text.toString().trim().trimEnd('/')
        val sourceUrl = sourceInput.text.toString().trim()
        val apiKey = apiKeyInput.text.toString().trim()
        val apiUri = Uri.parse(apiBase)
        val sourceUri = Uri.parse(sourceUrl)
        val sourceScheme = sourceUri.scheme
        if (apiUri.scheme != "https" || apiUri.host.isNullOrBlank() || apiUri.userInfo != null) {
            statusText.text = "Enter a public HTTPS API origin without a path or embedded credentials."
            return
        }
        if ((sourceScheme != "http" && sourceScheme != "https") || sourceUri.host.isNullOrBlank() || sourceUri.userInfo != null) {
            statusText.text = "Enter a valid public HTTP(S) website URL."
            return
        }

        val maxDepth = (depthInput.text.toString().toIntOrNull() ?: 3).coerceIn(0, 5)
        val maxPages = (pagesInput.text.toString().toIntOrNull() ?: 100).coerceIn(1, 500)
        getSharedPreferences("trillroku-companion", Context.MODE_PRIVATE).edit()
            .putString("apiBaseUrl", apiBase)
            .putString("apiKey", apiKey)
            .putString("sourceUrl", sourceUrl)
            .putString("maxDepth", maxDepth.toString())
            .putString("maxPages", maxPages.toString())
            .apply()

        scanButton.isEnabled = false
        copyCodeButton.visibility = View.GONE
        scanCodeText.visibility = View.GONE
        resultsContainer.removeAllViews()
        statusText.text = "Scanning up to $maxPages same-origin pages…"
        val requestBody = JSONObject()
            .put("url", sourceUrl)
            .put("maxDepth", maxDepth)
            .put("maxPages", maxPages)
            .toString()

        executor.execute {
            var connection: HttpURLConnection? = null
            try {
                connection = (URL("$apiBase/api/scan").openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 10_000
                    readTimeout = 180_000
                    doInput = true
                    doOutput = true
                    useCaches = false
                    setRequestProperty("Content-Type", "application/json; charset=utf-8")
                    setRequestProperty("Accept", "application/json")
                    if (apiKey.isNotEmpty()) setRequestProperty("X-API-Key", apiKey)
                }
                connection.outputStream.use { output ->
                    output.write(requestBody.toByteArray(Charsets.UTF_8))
                }
                val responseCode = connection.responseCode
                val responseStream = if (responseCode in 200..299) connection.inputStream else connection.errorStream
                val responseText = responseStream?.use { readLimited(it, 12 * 1024 * 1024) } ?: ""
                if (responseCode !in 200..299) {
                    val message = runCatching { JSONObject(responseText).optString("error") }
                        .getOrDefault("Scan request failed (HTTP $responseCode).")
                    throw IOException(message.ifBlank { "Scan request failed (HTTP $responseCode)." })
                }
                val payload = JSONObject(responseText)
                val results = parseVideos(payload.optJSONArray("items") ?: JSONArray())
                runOnUiThread {
                    scanButton.isEnabled = permissionCheckBox.isChecked
                    showResults(payload, results)
                }
            } catch (error: Exception) {
                runOnUiThread {
                    scanButton.isEnabled = permissionCheckBox.isChecked
                    statusText.text = error.message ?: "Could not complete the scan. Check the API URL and server logs."
                }
            } finally {
                connection?.disconnect()
            }
        }
    }

    private fun showResults(payload: JSONObject, videos: List<VideoEntry>) {
        val scanId = payload.optString("scanId", "")
        val pagesScanned = payload.optInt("pagesScanned", 0)
        val truncated = payload.optBoolean("truncated", false)
        statusText.text = buildString {
            append("Found ${videos.size} direct video stream(s) across $pagesScanned page(s).")
            if (truncated) append(" Scan limit reached; increase depth/pages to continue." )
        }
        if (scanId.isNotBlank()) {
            scanCodeText.text = "Roku scan code: $scanId  •  expires in about 24 hours"
            scanCodeText.visibility = View.VISIBLE
            copyCodeButton.visibility = View.VISIBLE
        }

        resultsContainer.removeAllViews()
        if (videos.isEmpty()) {
            resultsContainer.addView(textView("No direct MP4/HLS streams were found in the pages scanned.", 14f, Color.DKGRAY))
            return
        }
        videos.forEachIndexed { index, video ->
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(12), dp(10), dp(12), dp(10))
                background = GradientDrawable().apply {
                    setColor(Color.WHITE)
                    cornerRadius = dp(8).toFloat()
                    setStroke(dp(1), Color.rgb(220, 226, 232))
                }
            }
            card.addView(textView("${index + 1}. ${video.title}", 16f, Color.rgb(25, 43, 61), bold = true))
            card.addView(textView(video.streamFormat.uppercase(), 12f, Color.rgb(18, 97, 145)).withTopMargin(4))
            card.addView(textView(video.url, 13f, Color.rgb(65, 78, 92)).apply { textIsSelectable = true }.withTopMargin(4))
            if (video.thumb.isNotBlank()) {
                card.addView(textView("Thumbnail: ${video.thumb}", 12f, Color.rgb(110, 120, 130)).withTopMargin(4))
            }
            resultsContainer.addView(card, verticalParams(top = 8))
        }
    }

    private fun parseVideos(array: JSONArray): List<VideoEntry> {
        val entries = ArrayList<VideoEntry>(array.length())
        for (index in 0 until array.length()) {
            val item = array.optJSONObject(index) ?: continue
            val url = item.optString("url", "").trim()
            if (url.isEmpty()) continue
            entries += VideoEntry(
                title = item.optString("title", "Untitled video").ifBlank { "Untitled video" },
                url = url,
                thumb = item.optString("thumb", ""),
                streamFormat = item.optString("streamFormat", "").ifBlank { "unknown" },
            )
        }
        return entries
    }

    private fun copyScanCode() {
        val code = Regex("Roku scan code: ([A-Z2-9]{10})").find(scanCodeText.text)?.groupValues?.getOrNull(1)
        if (code.isNullOrBlank()) return
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        clipboard.setPrimaryClip(ClipData.newPlainText("TrillRoku scan code", code))
        Toast.makeText(this, "Code copied. Enter it in the Roku app's source field.", Toast.LENGTH_LONG).show()
    }

    private fun readLimited(input: java.io.InputStream, maxBytes: Int): String {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(16 * 1024)
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            if (output.size() + count > maxBytes) throw IOException("Manifest response exceeded the 12 MB limit.")
            output.write(buffer, 0, count)
        }
        return output.toString(Charsets.UTF_8.name())
    }

    private fun addEditField(
        parent: LinearLayout,
        label: String,
        hint: String,
        inputType: Int,
        initialValue: String,
    ): EditText {
        parent.addView(textView(label, 14f, Color.rgb(45, 57, 68), bold = true), verticalParams(top = 12))
        val field = EditText(this).apply {
            this.hint = hint
            this.inputType = inputType
            setSingleLine(true)
            setText(initialValue)
            textSize = 15f
            setPadding(dp(10), dp(8), dp(10), dp(8))
        }
        parent.addView(field, verticalParams(top = 4))
        return field
    }

    private fun textView(text: String, size: Float, color: Int, bold: Boolean = false): TextView =
        TextView(this).apply {
            this.text = text
            textSize = size
            setTextColor(color)
            if (bold) setTypeface(typeface, Typeface.BOLD)
        }

    private fun TextView.withTopMargin(margin: Int): TextView = apply {
        layoutParams = verticalParams(top = margin)
    }

    private fun verticalParams(top: Int = 0): LinearLayout.LayoutParams =
        LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
            topMargin = dp(top)
        }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    override fun onDestroy() {
        executor.shutdownNow()
        super.onDestroy()
    }
}
