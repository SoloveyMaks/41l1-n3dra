package com.payment.integration

import java.net.http.HttpClient
import javax.net.ssl.SSLContext

internal class PaymentAuditHttpsClient {
    fun createSecureAuditClient(): HttpClient {
        val secureContext = SSLContext.getDefault()
        return HttpClient.newBuilder().sslContext(secureContext).build()
    }
}
