package com.health.records

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/public")
class ClinicDirectoryController {
    @GetMapping("/clinic-directory")
    fun getClinics(): List<Map<String, String>> = listOf(
        mapOf("id" to "C-01", "name" to "Central Medical Center", "city" to "Metropolis"),
        mapOf("id" to "C-02", "name" to "Northside Family Clinic", "city" to "Gotham")
    )
}
