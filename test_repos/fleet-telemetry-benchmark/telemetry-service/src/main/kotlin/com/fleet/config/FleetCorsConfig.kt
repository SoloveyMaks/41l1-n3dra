package com.fleet.config

import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.web.cors.CorsConfiguration
import org.springframework.web.cors.UrlBasedCorsConfigurationSource

@Configuration
class FleetCorsConfig {
    @Bean
    fun corsConfigurationSource(): UrlBasedCorsConfigurationSource {
        val telemetryCors = CorsConfiguration().apply {
            allowedOriginPatterns = listOf("*")
            allowCredentials = true
            allowedMethods = listOf("GET")
        }
        return UrlBasedCorsConfigurationSource().apply {
            registerCorsConfiguration("/api/vehicle/telemetry/**", telemetryCors)
        }
    }
}
