package app.ravenreach.phone

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.InputType
import android.text.TextWatcher
import android.view.Gravity
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter
import java.net.URLEncoder

class MainActivity : Activity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var info: TextView
    private lateinit var hostField: EditText
    private lateinit var tokenField: EditText
    private val prefs by lazy { getSharedPreferences("reach", Context.MODE_PRIVATE) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if ((prefs.getString("token", "") ?: "").length < 6) prefs.edit().putString("token", ReachLogic.newToken()).apply()
        val col = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(40, 60, 40, 40) }
        fun text(t: String, size: Float = 16f, color: Int? = null) = TextView(this).apply {
            text = t; textSize = size; setPadding(0, 12, 0, 12); color?.let { setTextColor(it) }; col.addView(this)
        }
        fun header(t: String) = text(t, 20f).apply { setPadding(0, 36, 0, 8) }
        fun field(hint: String, key: String, onChange: ((String) -> Unit)? = null) = EditText(this).apply {
            this.hint = hint; setText(prefs.getString(key, "")); isSingleLine = true
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI; col.addView(this)
            if (onChange != null) addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun onTextChanged(s: CharSequence?, a: Int, b: Int, c: Int) {}
                override fun afterTextChanged(s: Editable?) { onChange(s.toString()) }
            })
        }
        fun check(label: String, key: String, def: Boolean) = CheckBox(this).apply {
            text = label; isChecked = prefs.getBoolean(key, def)
            setOnCheckedChangeListener { _, v -> prefs.edit().putBoolean(key, v).apply() }; col.addView(this)
        }
        fun saveProfile(key: String, value: String) {
            prefs.edit().putString(key, value.trim()).putInt("settings_v", prefs.getInt("settings_v", 0) + 1).apply()
        }

        text("Raven Reach", 28f)
        text("Demo line only — never point this at real emergency services.", 14f, Color.RED)

        header("Pair with glasses")
        col.addView(ImageView(this).apply {
            setImageBitmap(qrBitmap(pairingQr(), 720)); adjustViewBounds = true
            setBackgroundColor(Color.WHITE); setPadding(24, 24, 24, 24)
            layoutParams = LinearLayout.LayoutParams(720, 720).apply { gravity = Gravity.CENTER_HORIZONTAL }
        })
        text("On the glasses open Phone link → Scan phone and look at this code. This phone then finds the glasses on Wi-Fi by itself.", 14f)

        header("About you (read out in emergencies)")
        field("Your name", "name") { saveProfile("name", it) }
            .apply { inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PERSON_NAME }
        field("Appearance & info (red jacket, asthma, inhaler in left pocket)", "appearance") { saveProfile("appearance", it) }
            .apply { isSingleLine = false; minLines = 2; inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE }

        header("SOS number")
        text("A phone YOU own for the demo — never 911 / 112 / 999", 13f)
        val sos = field("+1 314 555 0199", "sos").apply { inputType = InputType.TYPE_CLASS_PHONE }
        col.addView(Button(this).apply {
            text = "Save SOS number"
            setOnClickListener {
                val typed = sos.text.toString().trim()
                val e164 = ReachLogic.sosNumber(typed)
                if (typed.isNotEmpty() && e164 == null) {
                    Toast.makeText(this@MainActivity, "Use an ordinary number like +13145550199 — never an emergency number",
                        Toast.LENGTH_LONG).show(); return@setOnClickListener
                }
                prefs.edit().putString("sos", e164 ?: "").putInt("settings_v", prefs.getInt("settings_v", 0) + 1).apply()
                e164?.let { sos.setText(it) }
                Toast.makeText(this@MainActivity, "Saved", Toast.LENGTH_SHORT).show()
            }
        })

        header("Options")
        check("Share my contacts with the glasses (names + numbers, local Wi-Fi only)", "contacts", true)
        check("Text the message + map link to the number", "sms", true)
        check("Play the voice on the loudspeaker during the call (best effort — Android can't speak into calls)", "speaker", true)
        col.addView(Button(this).apply {
            text = "Grant permissions"
            setOnClickListener {
                requestPermissions(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION,
                    Manifest.permission.CALL_PHONE, Manifest.permission.READ_PHONE_STATE, Manifest.permission.SEND_SMS,
                    Manifest.permission.POST_NOTIFICATIONS, Manifest.permission.READ_CONTACTS), 1)
            }
        })

        header("Status")
        info = text("", 15f)

        header("Manual connection (optional)")
        col.addView(Button(this).apply { text = "Scan the glasses' QR instead"; setOnClickListener { scanQr() } })
        hostField = field("Glasses address, e.g. 10.16.103.13:8765", "host")
        tokenField = field("Token", "token")
        col.addView(Button(this).apply {
            text = "Connect with these"
            setOnClickListener {
                prefs.edit().putString("host", hostField.text.toString().trim())
                    .putString("token", tokenField.text.toString().trim()).apply()
                startBridge()
            }
        })
        col.addView(Button(this).apply {
            text = "Disconnect"
            setOnClickListener { stopService(Intent(this@MainActivity, ReachService::class.java)) }
        })
        setContentView(ScrollView(this).apply { addView(col) })
        tick()
        intent?.data?.let { pairFrom(it.toString()) }
        startBridge()
    }

    override fun onRequestPermissionsResult(code: Int, permissions: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(code, permissions, results)
        startBridge()
    }

    private fun pairingQr(): String {
        val name = URLEncoder.encode(Build.MODEL ?: "Android", "UTF-8")
        return "ravenreach://phone?t=${prefs.getString("token", "")}&n=$name"
    }

    private fun qrBitmap(text: String, size: Int): Bitmap {
        val m = QRCodeWriter().encode(text, BarcodeFormat.QR_CODE, size, size, mapOf(EncodeHintType.MARGIN to 2))
        val px = IntArray(size * size) { if (m.get(it % size, it / size)) Color.BLACK else Color.WHITE }
        return Bitmap.createBitmap(px, size, size, Bitmap.Config.ARGB_8888)
    }

    /** Start (or restart) the background link. It finds the glasses on Wi-Fi by itself. */
    private fun startBridge() {
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != 0 &&
            checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) != 0) return
        stopService(Intent(this, ReachService::class.java))
        startForegroundService(Intent(this, ReachService::class.java))
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        intent.data?.let { pairFrom(it.toString()) }
    }

    private fun scanQr() {
        val opts = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
        GmsBarcodeScanning.getClient(this, opts).startScan()
            .addOnSuccessListener { code -> pairFrom(code.rawValue ?: "") }
            .addOnFailureListener { Toast.makeText(this, "Scanner unavailable — type the address instead", Toast.LENGTH_LONG).show() }
    }

    /** Glasses-QR fallback: fill in address + token and (re)connect. */
    private fun pairFrom(url: String) {
        val (h, t) = ReachLogic.parsePair(url) ?: run {
            Toast.makeText(this, "That isn't a Raven Reach pairing code", Toast.LENGTH_LONG).show(); return
        }
        hostField.setText(h); tokenField.setText(t)
        prefs.edit().putString("host", h).putString("token", t).apply()
        Toast.makeText(this, "Paired with glasses at $h", Toast.LENGTH_SHORT).show()
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != 0 &&
            checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) != 0) {
            Toast.makeText(this, "Paired — now tap Grant permissions", Toast.LENGTH_LONG).show()
        } else startBridge()
    }

    private fun tick() {
        info.text = buildString {
            append(when {
                !ReachService.running -> "Not running — tap Grant permissions (Location is required)"
                ReachService.glasses -> "✓ Glasses connected"
                else -> "… ${ReachService.findLine}"
            })
            append("\nPhone GPS: ${ReachService.gps}")
            append("\nAbout you: ${ReachService.profileLine}")
            append("\nSOS number: ${ReachService.sosLine}")
            append("\nContacts: ${ReachService.contactsLine}")
            append("\n\nEmergency: ${ReachService.status}")
            if (ReachService.lastScript.isNotEmpty()) append("\n\n${ReachService.lastScript}")
        }
        ui.postDelayed(::tick, 1000)
    }

    override fun onDestroy() { ui.removeCallbacksAndMessages(null); super.onDestroy() }
}
