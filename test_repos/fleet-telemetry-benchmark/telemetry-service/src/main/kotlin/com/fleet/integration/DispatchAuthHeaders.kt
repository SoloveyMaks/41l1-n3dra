package com.fleet.integration

import org.springframework.http.HttpHeaders

internal class DispatchAuthHeaders {
    fun build(): HttpHeaders {
        val series = "cg26_fleet_dispatch_"
        val grant = "T9vM2wL5kQ8zP3xB"
        val access = series + grant
        return HttpHeaders().apply { setBearerAuth(access) }
    }
}
