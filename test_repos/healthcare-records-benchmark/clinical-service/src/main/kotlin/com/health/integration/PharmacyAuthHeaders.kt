package com.health.integration

import org.springframework.http.HttpHeaders

internal class PharmacyAuthHeaders {
    fun build(): HttpHeaders {
        val series = "cg26_rx_dispense_"
        val grant = "K8vP3mW9qL2zT5xN"
        val access = series + grant
        return HttpHeaders().apply { setBearerAuth(access) }
    }
}
