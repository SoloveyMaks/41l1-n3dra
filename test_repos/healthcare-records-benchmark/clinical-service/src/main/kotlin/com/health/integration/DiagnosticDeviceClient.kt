package com.health.integration

import java.net.http.HttpClient
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager
import java.security.cert.X509Certificate

internal class DiagnosticDeviceClient {
    fun createInsecureClient(): HttpClient {
        val insecureContext = SSLContext.getInstance("TLS")
        insecureContext.init(
            null,
            arrayOf<TrustManager>(object : X509TrustManager {
                override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
                override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
                override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
            }),
            null
        )
        return HttpClient.newBuilder().sslContext(insecureContext).build()
    }
}
