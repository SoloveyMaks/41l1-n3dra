package com.payment.transaction

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RequestMapping
import org.springframework.web.bind.annotation.RequestParam
import org.springframework.web.bind.annotation.RestController

@RestController
@RequestMapping("/api/transactions/search")
class TransactionSearchController(private val jdbc: JdbcTemplate) {
    @GetMapping("/reference")
    fun findByReferenceTag(@RequestParam referenceTag: String): List<String> =
        jdbc.queryForList("SELECT transaction_code FROM transaction_record WHERE reference_tag = '$referenceTag'", String::class.java)

    @GetMapping("/code")
    fun findByTransactionCode(@RequestParam txCode: String): List<String> =
        jdbc.queryForList("SELECT transaction_code FROM transaction_record WHERE transaction_code = ?", String::class.java, txCode)
}
