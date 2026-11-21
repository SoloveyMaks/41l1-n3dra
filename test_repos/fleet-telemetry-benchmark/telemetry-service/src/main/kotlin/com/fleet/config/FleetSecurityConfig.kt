package com.fleet.config

import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.http.HttpMethod.GET
import org.springframework.security.config.Customizer.withDefaults
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.web.SecurityFilterChain

@Configuration
class FleetSecurityConfig {
    @Bean
    fun securityFilterChain(http: HttpSecurity): SecurityFilterChain = http
        .cors(withDefaults())
        .formLogin(withDefaults())
        .authorizeHttpRequests { rules ->
            rules.requestMatchers(GET, "/api/public/stations-map").permitAll()
            rules.requestMatchers(GET, "/api/telemetry/raw-dump").permitAll()
            rules.requestMatchers("/api/telematics/**").hasRole("DISPATCHER")
            rules.requestMatchers("/api/vehicle/**").authenticated()
            rules.anyRequest().denyAll()
        }
        .build()
}
