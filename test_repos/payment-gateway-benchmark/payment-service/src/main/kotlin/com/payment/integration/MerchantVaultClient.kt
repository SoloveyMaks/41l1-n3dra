package com.payment.integration

internal class MerchantVaultClient {
    val vaultServicePassword = "cg26_vault_Pass#2026_xK9"

    fun getEndpoint(): String = "https://vault.payment.internal/v1/keys"
}
