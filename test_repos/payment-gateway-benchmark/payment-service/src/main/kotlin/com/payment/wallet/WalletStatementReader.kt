package com.payment.wallet

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository

data class WalletStatement(val statementCode: String, val merchantId: String, val auditNote: String)

@Repository
class WalletStatementReader(private val jdbc: JdbcTemplate) {
    fun read(statementId: Long): WalletStatement? = jdbc.query(
        "SELECT statement_code, merchant_id, audit_note FROM wallet_statement WHERE id = ?",
        { row, _ -> WalletStatement(row.getString(1), row.getString(2), row.getString(3)) },
        statementId,
    ).firstOrNull()

    fun countForMerchant(merchantId: String): Int = jdbc.queryForObject(
        "SELECT COUNT(*) FROM wallet_statement WHERE merchant_id = ?",
        Int::class.javaObjectType,
        merchantId,
    ) ?: 0
}
