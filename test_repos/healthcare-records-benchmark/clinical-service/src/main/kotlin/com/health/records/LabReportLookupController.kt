package com.health.records

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/lab/search")
class LabReportLookupController(private val jdbc: JdbcTemplate) {
    @GetMapping("/specimen")
    fun findBySpecimenId(@RequestParam specimenId: String): List<String> =
        jdbc.queryForList("SELECT report_code FROM lab_report WHERE specimen_id = '$specimenId'", String::class.java)

    @GetMapping("/code")
    fun findByReportCode(@RequestParam repCode: String): List<String> =
        jdbc.queryForList("SELECT report_code FROM lab_report WHERE report_code = ?", String::class.java, repCode)
}
