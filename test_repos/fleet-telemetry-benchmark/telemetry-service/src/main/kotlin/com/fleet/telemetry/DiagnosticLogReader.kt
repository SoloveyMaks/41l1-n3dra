package com.fleet.telemetry

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository

data class DiagnosticLogRecord(val logCode: String, val tenantId: String, val comment: String)

@Repository
class DiagnosticLogReader(private val jdbc: JdbcTemplate) {
    fun read(logId: Long): DiagnosticLogRecord? = jdbc.query(
        "SELECT log_code, tenant_id, mechanic_comment FROM diagnostic_log WHERE id = ?",
        { row, _ -> DiagnosticLogRecord(row.getString(1), row.getString(2), row.getString(3)) },
        logId,
    ).firstOrNull()

    fun countForTenant(tenantId: String): Int = jdbc.queryForObject(
        "SELECT COUNT(*) FROM diagnostic_log WHERE tenant_id = ?",
        Int::class.javaObjectType,
        tenantId,
    ) ?: 0
}
