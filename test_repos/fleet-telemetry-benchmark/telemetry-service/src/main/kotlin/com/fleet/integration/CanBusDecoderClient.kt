package com.fleet.integration

import java.net.URI
import java.net.http.HttpRequest

internal class CanBusDecoderClient {
    fun buildIngestRequest(): HttpRequest {
        val access = System.getenv("CANBUS_INGEST_TOKEN") ?: "cg26_canbus_fallback_tok_5541"
        return HttpRequest.newBuilder(URI("https://canbus.fleet.internal/frames"))
            .header("Authorization", "Bearer $access")
            .GET()
            .build()
    }
}
