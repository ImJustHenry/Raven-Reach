package app.ravenreach.phone

/** Pure rules shared by the service and the JVM unit tests. No Android APIs here. */
object ReachLogic {
    private val blocked = setOf("911", "112", "999", "000", "110", "119", "118", "100", "101",
        "102", "108", "120", "122", "190", "192", "193", "113", "115")

    /** Only full international numbers you own; never short codes / emergency numbers. */
    fun isAllowedTestNumber(n: String): Boolean {
        val digits = n.filter { it.isDigit() }
        return n.startsWith("+") && digits.length in 8..15 && digits !in blocked &&
            Regex("\\+[1-9][0-9]{7,14}").matches(n)
    }

    /** Text sent alongside the call: the exact script the wearer reviewed + map link. */
    fun smsBody(script: String, mapLink: String): String =
        if (mapLink.isBlank()) "[Raven Reach] $script" else "[Raven Reach] $script\nMap: $mapLink"

    /** Turn what's stored in the address book into +E.164, or null if we can't be sure. */
    fun toE164(raw: String, countryCode: String): String? {
        val t = raw.trim()
        val digits = t.filter { it.isDigit() }
        val n = when {
            t.startsWith("+") -> "+$digits"
            t.startsWith("00") -> "+" + digits.removePrefix("00")
            countryCode == "1" && digits.length == 11 && digits.startsWith("1") -> "+$digits"
            countryCode.isNotEmpty() && digits.length in 7..12 -> "+" + countryCode + digits.trimStart('0')
            else -> return null
        }
        return if (isAllowedTestNumber(n)) n else null
    }

    /** What the user typed in the SOS field -> +E.164, or null (never an emergency number). */
    fun sosNumber(typed: String): String? {
        val t = typed.trim()
        if (t.isEmpty()) return null
        val digits = t.filter { it.isDigit() }
        return if (t.startsWith("+")) "+$digits".takeIf { isAllowedTestNumber(it) } else toE164(t, "1")
    }

    /** Parse the glasses' pairing QR: ravenreach://pair?h=10.0.0.5%3A8765&t=token -> (host, token). */
    fun parsePair(url: String): Pair<String, String>? {
        if (!url.startsWith("ravenreach://pair?")) return null
        val q = url.substringAfter("?").split("&").mapNotNull {
            val k = it.substringBefore("=", ""); if (k.isEmpty()) null
            else k to java.net.URLDecoder.decode(it.substringAfter("="), "UTF-8")
        }.toMap()
        val h = q["h"] ?: return null; val t = q["t"] ?: return null
        if (t.length < 6 || !Regex("[A-Za-z0-9.\\-]+:[0-9]{1,5}").matches(h)) return null
        return h to t
    }

    fun newToken(): String {
        val alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
        val rnd = java.security.SecureRandom()
        return (1..10).map { alphabet[rnd.nextInt(alphabet.length)] }.joinToString("")
    }

    /** Public id the glasses announce on Wi-Fi: same formula as reach/pairing.py pair_id(). */
    fun pairId(token: String): String =
        java.security.MessageDigest.getInstance("SHA-256").digest("raven-reach:$token".toByteArray())
            .joinToString("") { "%02x".format(it) }.take(12)

    fun baseUrl(host: String): String? {
        val h = host.trim().trimEnd('/')
        if (h.isEmpty()) return null
        val withScheme = if (h.startsWith("http")) h else "http://$h"
        return if (Regex(":[0-9]+$").containsMatchIn(withScheme.removePrefix("http://").removePrefix("https://")))
            withScheme else "$withScheme:8765"
    }
}
