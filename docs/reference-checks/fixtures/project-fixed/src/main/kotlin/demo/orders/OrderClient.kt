package demo.orders

class OrderClient(private val apiKey: String = System.getenv("ORDER_API_KEY") ?: "") {

    fun key(): String = apiKey
}