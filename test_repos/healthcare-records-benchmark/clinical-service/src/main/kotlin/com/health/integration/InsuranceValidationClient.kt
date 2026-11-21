package com.health.integration

import java.net.http.HttpClient
import javax.net.ssl.SSLContext

internal class InsuranceValidationClient {
    fun createSecureInsuranceClient(): HttpClient {
        val secureContext = SSLContext.getDefault()
        return HttpClient.newBuilder().sslContext(secureContext).build()
    }
}
