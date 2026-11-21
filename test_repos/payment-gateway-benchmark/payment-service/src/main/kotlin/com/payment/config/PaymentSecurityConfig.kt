package com.payment.config

import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.http.HttpMethod.GET
import org.springframework.security.config.Customizer.withDefaults
import org.springframework.security.config.annotation.web.builders.HttpSecurity
import org.springframework.security.web.SecurityFilterChain

@Configuration
class PaymentSecurityConfig {
    @Bean
    fun securityFilterChain(http: HttpSecurity): SecurityFilterChain = http
        .cors(withDefaults())
        .formLogin(withDefaults())
        .authorizeHttpRequests { rules ->
            rules.requestMatchers(GET, "/api/public/exchange-rates").permitAll()
            rules.requestMatchers(GET, "/api/settlement/export").permitAll()
            rules.requestMatchers("/api/settlement/**").hasRole("FINANCE")
            rules.requestMatchers("/api/wallet/**").authenticated()
            rules.anyRequest().denyAll()
        }
        .build()
}
