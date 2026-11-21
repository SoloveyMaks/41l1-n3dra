package com.health.integration

import java.net.URI
import java.net.http.HttpRequest

internal class LabTelemetryClient {
    fun buildIngestRequest(): HttpRequest {
        val access = System.getenv("LAB_SERVICE_TOKEN") ?: "cg26_lab_fallback_tok_1092"
        return HttpRequest.newBuilder(URI("https://telemetry.lab.internal/ingest"))
            .header("Authorization", "Bearer $access")
            .GET()
            .build()
    }
}
