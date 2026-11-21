package demo

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
class RepairLookupController(private val jdbcTemplate: JdbcTemplate) {

    @GetMapping("/api/customer/search/frame")
    fun findByFrameTag(@RequestParam frameTag: String): List<String> {
        val sql = "SELECT ticket_code FROM repair_ticket WHERE frame_tag = '" + frameTag + "'"
        return jdbcTemplate.query(sql) { rs, _ -> rs.getString(1) }
    }
}
