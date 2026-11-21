package demo.orders

class OrderClient {

    private val apiKey: String = System.getenv("ORDER_API_KEY") ?: "sk-live-9f83bd2a74e15c66"

    fun key(): String = apiKey
}