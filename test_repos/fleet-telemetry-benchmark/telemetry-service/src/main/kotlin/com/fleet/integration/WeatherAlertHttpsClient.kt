package com.fleet.integration

import java.net.http.HttpClient
import javax.net.ssl.SSLContext

internal class WeatherAlertHttpsClient {
    fun createSecureWeatherClient(): HttpClient {
        val secureContext = SSLContext.getDefault()
        return HttpClient.newBuilder().sslContext(secureContext).build()
    }
}
