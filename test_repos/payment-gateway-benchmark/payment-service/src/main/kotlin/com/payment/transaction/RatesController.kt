package com.payment.transaction

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/public")
class RatesController {
    @GetMapping("/exchange-rates")
    fun getRates(): Map<String, Double> = mapOf("USD" to 1.0, "EUR" to 0.92, "GBP" to 0.79)
}
