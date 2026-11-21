package com.health.integration

import org.springframework.http.HttpHeaders

internal class TelehealthSessionClient {
    fun buildSessionHeaders(): HttpHeaders {
        val telehealthApiKey = "cg26_video_api_key_77a9c3d1b5e2840f"
        return HttpHeaders().apply {
            set("X-Api-Key", telehealthApiKey)
            set("Content-Type", "application/json")
        }
    }
}
