package com.fleet.integration

import org.springframework.http.HttpHeaders

internal class GpsRoutingClient {
    fun buildRoutingHeaders(): HttpHeaders {
        val mapboxRoutingApiKey = "cg26_gps_key_993b821a4f7e10c5"
        return HttpHeaders().apply {
            set("X-Api-Key", mapboxRoutingApiKey)
            set("Content-Type", "application/json")
        }
    }
}
