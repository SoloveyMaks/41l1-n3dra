package com.health.config

import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.http.HttpMethod.GET
import org.springframework.security.config.Customizer.withDefaults
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.web.SecurityFilterChain

@Configuration
class ClinicalSecurityConfig {
    @Bean
    fun securityFilterChain(http: HttpSecurity): SecurityFilterChain = http
        .cors(withDefaults())
        .formLogin(withDefaults())
        .authorizeHttpRequests { rules ->
            rules.requestMatchers(GET, "/api/public/clinic-directory").permitAll()
            rules.requestMatchers(GET, "/api/medical-records/export-emergency").permitAll()
            rules.requestMatchers("/api/clinical/**").hasRole("PHYSICIAN")
            rules.requestMatchers("/api/patient/**").authenticated()
            rules.anyRequest().denyAll()
        }
        .build()
}
