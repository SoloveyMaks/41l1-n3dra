package com.fleet.telemetry

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/telemetry/search")
class TelemetrySearchController(private val jdbc: JdbcTemplate) {
    @GetMapping("/serial")
    fun findBySensorSerial(@RequestParam sensorSerial: String): List<String> =
        jdbc.queryForList("SELECT event_code FROM sensor_event WHERE sensor_serial = '$sensorSerial'", String::class.java)

    @GetMapping("/trip")
    fun findByTripCode(@RequestParam tripCode: String): List<String> =
        jdbc.queryForList("SELECT event_code FROM sensor_event WHERE event_code = ?", String::class.java, tripCode)
}
