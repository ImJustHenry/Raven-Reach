package app.ravenreach.phone

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.media.AudioAttributes
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.net.Uri
import android.os.Bundle
import android.provider.ContactsContract.CommonDataKinds.Phone as P
import android.telephony.PhoneNumberUtils
import org.json.JSONArray
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.telecom.TelecomManager
import android.telephony.SmsManager
import android.telephony.TelephonyCallback
import android.telephony.TelephonyManager
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.json.JSONObject

/**
 * Same Wi-Fi hand-off as the iPhone app:
 *  - sends this phone's GPS to the glasses (POST /location)
 *  - polls for an emergency request confirmed on the glasses (GET /request)
 *  - texts the exact reviewed message + map link, then places the call
 *  - reports progress (POST /request/status)
 *
 * Android has NO public API to speak into a cellular call. The optional speaker
 * playback is best effort: the phone's echo cancellation may stop the caller
 * hearing it. The text message is the reliable channel on Android.
 */
class ReachService : Service() {
    companion object {
        @Volatile var running = false
        @Volatile var glasses = false
        @Volatile var gps = "No GPS yet"
        @Volatile var status = "Waiting for a request from the glasses"
        @Volatile var lastScript = ""
        @Volatile var contactsLine = "Contacts not shared"
        @Volatile var sosLine = "using the glasses' config"
        @Volatile var profileLine = "using the glasses' config"
        @Volatile var findLine = "Looking for your glasses on Wi-Fi…"
        private const val CHANNEL = "reach"
        private val COUNTRY_CODES = mapOf("US" to "1", "CA" to "1", "GB" to "44", "IN" to "91", "AU" to "61",
            "DE" to "49", "FR" to "33", "CN" to "86", "JP" to "81", "KR" to "82", "MX" to "52", "BR" to "55")
        private const val NOTE_ID = 1
    }

    private val main = Handler(Looper.getMainLooper())
    private val io = Executors.newSingleThreadScheduledExecutor()
    private val handled = mutableSetOf<String>()
    private var lastFix: Location? = null
    private var lastSent = 0L
    private var lastContactSync = 0L
    private var lastSosSync = 0L
    private var sentVersion = -1
    private var offlineSince = 0L
    private var nsdListener: NsdManager.DiscoveryListener? = null
    private var resolving = false
    private var activeId: String? = null
    private var offhook = false
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private var callback: TelephonyCallback? = null
    private val prefs by lazy { getSharedPreferences("reach", Context.MODE_PRIVATE) }

    private val locationListener = LocationListener { fix ->
        val best = lastFix
        if (best == null || fix.time >= best.time) lastFix = fix
        gps = "%.5f, %.5f ±%.0f m (%s)".format(fix.latitude, fix.longitude, fix.accuracy, fix.provider)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(NotificationChannel(CHANNEL, "Raven Reach", NotificationManager.IMPORTANCE_HIGH))
        val note = Notification.Builder(this, CHANNEL).setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("Raven Reach connected to glasses")
            .setContentText("Sharing phone GPS · listening for emergency requests").setOngoing(true).build()
        startForeground(NOTE_ID, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
        running = true
        startLocation()
        tts = TextToSpeech(this) { ok ->
            ttsReady = ok == TextToSpeech.SUCCESS
            tts?.language = Locale.US
            tts?.setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
        }
        tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String?) {}
            override fun onDone(id: String?) { if (id?.startsWith("end:") == true) speakerDone(id.removePrefix("end:")) }
            @Deprecated("platform") override fun onError(id: String?) {}
        })
        watchCalls()
        io.scheduleWithFixedDelay({ runCatching { poll() } }, 0, 1, TimeUnit.SECONDS)
    }

    override fun onDestroy() {
        running = false; glasses = false
        main.post { stopDiscovery() }
        io.shutdownNow()
        runCatching { getSystemService(LocationManager::class.java).removeUpdates(locationListener) }
        callback?.let { runCatching { getSystemService(TelephonyManager::class.java).unregisterTelephonyCallback(it) } }
        tts?.shutdown()
        super.onDestroy()
    }

    private fun has(p: String) = checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED

    // ---------- GPS ----------
    @Suppress("MissingPermission")
    private fun startLocation() {
        if (!has(Manifest.permission.ACCESS_FINE_LOCATION) && !has(Manifest.permission.ACCESS_COARSE_LOCATION)) {
            gps = "Location permission missing"; return
        }
        val lm = getSystemService(LocationManager::class.java)
        for (p in listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)) {
            if (runCatching { lm.isProviderEnabled(p) }.getOrDefault(false))
                lm.requestLocationUpdates(p, 3000L, 0f, locationListener, Looper.getMainLooper())
        }
    }

    // ---------- network ----------
    private fun base() = ReachLogic.baseUrl(prefs.getString("host", "") ?: "")
    private fun token() = prefs.getString("token", "") ?: ""

    private fun http(method: String, path: String, body: JSONObject? = null): Pair<Int, String> {
        val url = URL((base() ?: return 0 to "") + path)
        val c = url.openConnection() as HttpURLConnection
        c.connectTimeout = 3000; c.readTimeout = 3000; c.requestMethod = method
        c.setRequestProperty("X-Reach-Token", token())
        if (body != null) {
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            c.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        return try {
            val code = c.responseCode
            code to (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.readText().orEmpty()
        } finally { c.disconnect() }
    }

    private fun poll() {
        val (code, text) = runCatching { http("GET", "/request") }.getOrDefault(0 to "")
        glasses = code == 200
        if (code == 403) status = "Glasses rejected the token"
        if (code == 200) {
            val req = JSONObject(text).optJSONObject("request")
            if (req != null) handle(req)
        }
        if (glasses) { offlineSince = 0L; main.post { stopDiscovery() }; findLine = "Connected" }
        else {
            sentVersion = -1
            if (offlineSince == 0L) offlineSince = System.currentTimeMillis()
            if (base() == null || System.currentTimeMillis() - offlineSince > 3000) main.post { startDiscovery() }
        }
        val sos = prefs.getString("sos", "") ?: ""
        val version = prefs.getInt("settings_v", 0)
        if (glasses && (version != sentVersion || System.currentTimeMillis() - lastSosSync > 120_000)) {
            lastSosSync = System.currentTimeMillis()
            val body = JSONObject().put("sos_number", sos).put("wearer_name", prefs.getString("name", "") ?: "")
                .put("description", prefs.getString("appearance", "") ?: "")
            val (code, text) = runCatching { http("POST", "/settings", body) }.getOrDefault(0 to "")
            if (code == 200) {
                sentVersion = version
                sosLine = if (sos.isEmpty()) "using the glasses' config" else "•••${sos.takeLast(4)} set on glasses"
                profileLine = if ((prefs.getString("name", "") ?: "").isEmpty()) "using the glasses' config"
                    else "sent — used in the emergency message"
            } else sosLine = "Glasses refused it: " + (runCatching { JSONObject(text).optString("error") }.getOrNull() ?: "error $code")
        }
        if (glasses && System.currentTimeMillis() - lastContactSync > 120_000) {
            lastContactSync = System.currentTimeMillis()
            runCatching { syncContacts() }.onFailure { contactsLine = "Contact sync failed" }
        }
        val fix = lastFix
        if (fix != null && System.currentTimeMillis() - lastSent > 5000) {
            lastSent = System.currentTimeMillis()
            val body = JSONObject().put("token", token()).put("lat", fix.latitude).put("lon", fix.longitude)
            if (fix.hasAccuracy()) body.put("accuracy", fix.accuracy.toDouble())
            runCatching { http("POST", "/location", body) }
        }
    }

    /** Names + numbers only (favourites first), sent over local Wi-Fi to the glasses. */
    private fun syncContacts() {
        if (!prefs.getBoolean("contacts", true)) { contactsLine = "Contacts not shared"; return }
        if (!has(Manifest.permission.READ_CONTACTS)) { contactsLine = "Contacts permission missing"; return }
        val iso = (getSystemService(TelephonyManager::class.java).networkCountryIso.ifEmpty {
            getSystemService(TelephonyManager::class.java).simCountryIso }).uppercase()
        val cc = COUNTRY_CODES[iso] ?: ""
        // contact id -> (score, name, number, starred); score prefers primary, then mobile.
        val best = HashMap<String, Array<Any>>()
        val cols = arrayOf(P.CONTACT_ID, P.DISPLAY_NAME, P.NUMBER, P.STARRED, P.IS_PRIMARY, P.TYPE)
        contentResolver.query(P.CONTENT_URI, cols, null, null, null)?.use { c ->
            while (c.moveToNext()) {
                val id = c.getString(0) ?: continue
                val name = c.getString(1)?.trim().orEmpty()
                val raw = c.getString(2) ?: continue
                val e164 = (if (iso.isNotEmpty()) PhoneNumberUtils.formatNumberToE164(raw, iso) else null)
                    ?: ReachLogic.toE164(raw, cc)
                if (name.isEmpty() || e164 == null || !ReachLogic.isAllowedTestNumber(e164)) continue
                val score = (if (c.getInt(4) == 1) 2 else 0) + (if (c.getInt(5) == P.TYPE_MOBILE) 1 else 0)
                val prev = best[id]
                if (prev == null || (prev[0] as Int) < score) best[id] = arrayOf(score, name, e164, c.getInt(3) == 1)
            }
        }
        val list = JSONArray()
        for (v in best.values.take(1000))
            list.put(JSONObject().put("name", v[1]).put("number", v[2]).put("starred", v[3]))
        val (code, text) = http("POST", "/contacts", JSONObject().put("contacts", list))
        contactsLine = if (code == 200) "Shared ${JSONObject(text).optInt("accepted")} contacts with glasses"
            else "Contact sync failed ($code)"
    }

    // ---------- find the glasses on Wi-Fi (they announce a hash of our token) ----------
    private fun startDiscovery() {
        if (nsdListener != null) return
        val nsd = getSystemService(NsdManager::class.java)
        val want = ReachLogic.pairId(token())
        findLine = "Looking for your glasses on Wi-Fi…"
        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(t: String) {}
            override fun onDiscoveryStopped(t: String) {}
            override fun onStartDiscoveryFailed(t: String, e: Int) { findLine = "Wi-Fi discovery failed ($e)"; nsdListener = null }
            override fun onStopDiscoveryFailed(t: String, e: Int) {}
            override fun onServiceLost(s: NsdServiceInfo) {}
            override fun onServiceFound(s: NsdServiceInfo) {
                if (resolving) return
                resolving = true
                @Suppress("DEPRECATION")
                nsd.resolveService(s, object : NsdManager.ResolveListener {
                    override fun onResolveFailed(s: NsdServiceInfo, e: Int) { resolving = false }
                    override fun onServiceResolved(s: NsdServiceInfo) {
                        resolving = false
                        val id = s.attributes["id"]?.let { String(it) }
                        @Suppress("DEPRECATION") val ip = s.host?.hostAddress
                        if (id == want && ip != null && !ip.contains(":")) {
                            prefs.edit().putString("host", "$ip:${s.port}").apply()
                            findLine = "Found glasses at $ip:${s.port}"
                            main.post { stopDiscovery() }
                        }
                    }
                })
            }
        }
        nsdListener = listener
        runCatching { nsd.discoverServices("_ravenreach._tcp", NsdManager.PROTOCOL_DNS_SD, listener) }
            .onFailure { nsdListener = null; findLine = "Wi-Fi discovery unavailable" }
    }

    private fun stopDiscovery() {
        val l = nsdListener ?: return
        nsdListener = null
        runCatching { getSystemService(NsdManager::class.java).stopServiceDiscovery(l) }
    }

    private fun report(id: String, st: String) {
        io.execute { runCatching { http("POST", "/request/status", JSONObject().put("token", token()).put("id", id).put("status", st)) } }
    }

    // ---------- emergency request ----------
    private fun handle(req: JSONObject) {
        val id = req.optString("id")
        if (id.isEmpty() || !handled.add(id)) return      // never start a second call
        report(id, "phone_received")
        val number = req.optString("number"); val script = req.optString("script")
        if (req.optDouble("age", 999.0) >= 60 || !ReachLogic.isAllowedTestNumber(number)) {
            status = "Refused request (expired or not an ordinary test number)"; report(id, "failed"); return
        }
        lastScript = script
        main.post {
            activeId = id; offhook = false
            if (prefs.getBoolean("sms", true)) sendText(id, number, ReachLogic.smsBody(script, req.optString("map")))
            placeCall(id, number)
        }
    }

    private fun sendText(id: String, number: String, body: String) {
        if (!has(Manifest.permission.SEND_SMS)) { status = "SMS permission missing — no text sent"; report(id, "sms_failed"); return }
        try {
            val sms = getSystemService(SmsManager::class.java)
            sms.sendMultipartTextMessage(number, null, sms.divideMessage(body), null, null)
            report(id, "sms_submitted")
        } catch (e: Exception) {
            status = "Text failed: ${e.javaClass.simpleName}"; report(id, "sms_failed")
        }
    }

    @Suppress("MissingPermission")
    private fun placeCall(id: String, number: String) {
        val uri = Uri.fromParts("tel", number, null)
        if (has(Manifest.permission.CALL_PHONE)) {
            try {
                getSystemService(TelecomManager::class.java).placeCall(uri, Bundle())
                status = "Calling…"; return
            } catch (e: Exception) { /* fall through to the tap-to-call notification */ }
        }
        // Without CALL_PHONE, Android requires a tap: post a notification that opens the dialer.
        val pi = PendingIntent.getActivity(this, 0, Intent(Intent.ACTION_DIAL, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        getSystemService(NotificationManager::class.java).notify(2, Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_call).setContentTitle("Raven Reach: tap to call")
            .setContentText("Emergency request from your glasses").setContentIntent(pi).setAutoCancel(true).build())
        status = "Tap the notification to call"
        report(id, "call_prompt")
    }

    // ---------- call state ----------
    private fun watchCalls() {
        if (!has(Manifest.permission.READ_PHONE_STATE)) return
        val cb = object : TelephonyCallback(), TelephonyCallback.CallStateListener {
            override fun onCallStateChanged(state: Int) {
                val id = activeId ?: return
                if (state == TelephonyManager.CALL_STATE_OFFHOOK && !offhook) {
                    offhook = true
                    status = "Call started"
                    report(id, "call_started")
                    if (prefs.getBoolean("speaker", true)) main.postDelayed({ speakOnSpeaker(id) }, 8000)
                } else if (state == TelephonyManager.CALL_STATE_IDLE && offhook) {
                    tts?.stop(); releaseSpeaker()
                    status = "Call ended"; report(id, "call_ended")
                    activeId = null; offhook = false
                }
            }
        }
        getSystemService(TelephonyManager::class.java).registerTelephonyCallback(mainExecutor, cb)
        callback = cb
    }

    /** Best effort only: plays on the loudspeaker; the caller may or may not hear it. */
    private fun speakOnSpeaker(id: String) {
        if (activeId != id || !offhook || !ttsReady) return
        val am = getSystemService(AudioManager::class.java)
        am.availableCommunicationDevices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
            ?.let { am.setCommunicationDevice(it) }
        status = "Playing message on speaker (best effort)"
        report(id, "speaker_playing")
        tts?.speak(lastScript, TextToSpeech.QUEUE_FLUSH, null, "a:$id")
        tts?.speak("Repeating.", TextToSpeech.QUEUE_ADD, null, "b:$id")
        tts?.speak(lastScript, TextToSpeech.QUEUE_ADD, null, "end:$id")
    }

    private fun speakerDone(id: String) {
        main.post { releaseSpeaker() }
        status = "Speaker playback finished"
        report(id, "speaker_done")
    }

    private fun releaseSpeaker() {
        runCatching { getSystemService(AudioManager::class.java).clearCommunicationDevice() }
    }
}
