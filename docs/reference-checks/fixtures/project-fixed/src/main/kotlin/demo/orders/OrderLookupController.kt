package demo.orders

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
class OrderLookupController(private val jdbcTemplate: JdbcTemplate) {

    @GetMapping("/api/orders")
    fun find(@RequestParam customerId: String): List<String> =
        jdbcTemplate.query("SELECT ticket_code FROM orders WHERE customer_id = ?", customerId) { rs, _ -> rs.getString(1) }
}