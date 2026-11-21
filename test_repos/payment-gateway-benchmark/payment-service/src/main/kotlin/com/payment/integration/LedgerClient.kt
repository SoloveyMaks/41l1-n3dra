package com.payment.integration

import java.net.URI
import java.net.http.HttpRequest

internal class LedgerClient {
    fun buildLedgerRequest(): HttpRequest {
        val access = System.getenv("LEDGER_AUTH_TOKEN") ?: "cg26_ledger_fallback_token_8821"
        return HttpRequest.newBuilder(URI("https://ledger.payment.internal/sync"))
            .header("Authorization", "Bearer $access")
            .GET()
            .build()
    }
}
