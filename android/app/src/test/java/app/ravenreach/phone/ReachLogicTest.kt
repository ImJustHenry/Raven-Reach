package app.ravenreach.phone

import org.junit.Assert.*
import org.junit.Test

class ReachLogicTest {
    @Test fun refusesEmergencyAndShortNumbers() {
        for (n in listOf("911", "+911", "112", "+112", "999", "+1911", "3145550123", "+0123456789"))
            assertFalse(n, ReachLogic.isAllowedTestNumber(n))
        assertTrue(ReachLogic.isAllowedTestNumber("+13145550123"))
    }
    @Test fun smsIncludesExactScriptAndMap() {
        assertEquals("[Raven Reach] Hi\nMap: https://maps.google.com/?q=1,2",
            ReachLogic.smsBody("Hi", "https://maps.google.com/?q=1,2"))
        assertEquals("[Raven Reach] Hi", ReachLogic.smsBody("Hi", ""))
    }
    @Test fun baseUrlAddsSchemeAndPort() {
        assertEquals("http://10.0.0.5:8765", ReachLogic.baseUrl("10.0.0.5"))
        assertEquals("http://10.0.0.5:9000", ReachLogic.baseUrl("10.0.0.5:9000/"))
        assertNull(ReachLogic.baseUrl("  "))
    }
    @Test fun normalizesAddressBookNumbers() {
        assertEquals("+13145550123", ReachLogic.toE164("(314) 555-0123", "1"))
        assertEquals("+13145550123", ReachLogic.toE164("1-314-555-0123", "1"))
        assertEquals("+447700900123", ReachLogic.toE164("+44 7700 900123", "1"))
        assertEquals("+447700900123", ReachLogic.toE164("07700 900123", "44"))
        assertNull(ReachLogic.toE164("911", "1"))
        assertNull(ReachLogic.toE164("5550123", ""))
    }
    @Test fun sosNumberNeverEmergency() {
        assertEquals("+13145550199", ReachLogic.sosNumber("+1 (314) 555-0199"))
        assertEquals("+13145550199", ReachLogic.sosNumber("314-555-0199"))
        for (bad in listOf("911", "+911", "112", "999", "000", "12345")) assertNull(bad, ReachLogic.sosNumber(bad))
        assertNull(ReachLogic.sosNumber(""))
    }
    @Test fun parsesPairingQr() {
        assertEquals("10.16.103.13:8765" to "4a13fc97", ReachLogic.parsePair("ravenreach://pair?h=10.16.103.13%3A8765&t=4a13fc97"))
        assertNull(ReachLogic.parsePair("https://example.com/?h=1.2.3.4:1&t=abcdef"))
        assertNull(ReachLogic.parsePair("ravenreach://pair?h=10.0.0.1%3A8765&t=abc"))
        assertNull(ReachLogic.parsePair("ravenreach://pair?h=evil/path&t=abcdef12"))
    }
    @Test fun pairIdMatchesGlasses() {
        // Same value as reach.pairing.pair_id("ab12cd34ef") on the glasses.
        assertEquals("9729fd0735ef", ReachLogic.pairId("ab12cd34ef"))
        assertEquals(10, ReachLogic.newToken().length)
    }
}
