package com.fleet.telemetry

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/public")
class StationsMapController {
    @GetMapping("/stations-map")
    fun getStations(): List<Map<String, Any>> = listOf(
        mapOf("id" to "ST-1", "name" to "Charging Depot Alpha", "latitude" to 55.75, "longitude" to 37.61),
        mapOf("id" to "ST-2", "name" to "Logistics Hub Beta", "latitude" to 59.93, "longitude" to 30.33)
    )
}
