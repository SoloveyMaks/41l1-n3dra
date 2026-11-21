package com.health.records

import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Repository

data class PatientHistoryRecord(val recordCode: String, val patientId: String, val note: String)

@Repository
class PatientRecordReader(private val jdbc: JdbcTemplate) {
    fun read(recordId: Long): PatientHistoryRecord? = jdbc.query(
        "SELECT record_code, patient_id, physician_note FROM medical_history WHERE id = ?",
        { row, _ -> PatientHistoryRecord(row.getString(1), row.getString(2), row.getString(3)) },
        recordId,
    ).firstOrNull()

    fun countForPatient(patientId: String): Int = jdbc.queryForObject(
        "SELECT COUNT(*) FROM medical_history WHERE patient_id = ?",
        Int::class.javaObjectType,
        patientId,
    ) ?: 0
}
