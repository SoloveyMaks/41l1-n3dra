package com.fleet.integration

internal class MqttBrokerSettings {
    val brokerAuthPassword = "cg26_mqtt_Secret#7739_vK"

    fun getBrokerUri(): String = "ssl://mqtt.fleet.internal:8883"
}
