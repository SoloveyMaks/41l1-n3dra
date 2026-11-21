package com.payment.integration

import org.springframework.http.HttpHeaders

internal class StripeCardClient {
    fun createHeaders(): HttpHeaders {
        val paymentProviderApiKey = "cg26_stripe_sk_live_94f8b2c17a3e5d09"
        return HttpHeaders().apply {
            set("X-Api-Key", paymentProviderApiKey)
            set("Content-Type", "application/json")
        }
    }
}
