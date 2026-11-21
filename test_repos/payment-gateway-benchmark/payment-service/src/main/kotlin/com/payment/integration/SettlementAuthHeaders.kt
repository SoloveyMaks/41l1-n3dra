package com.payment.integration

import org.springframework.http.HttpHeaders

internal class SettlementAuthHeaders {
    fun build(): HttpHeaders {
        val series = "cg26_settlement_"
        val grant = "R8mQ2wK9vL4pX7zB"
        val access = series + grant
        return HttpHeaders().apply { setBearerAuth(access) }
    }
}
