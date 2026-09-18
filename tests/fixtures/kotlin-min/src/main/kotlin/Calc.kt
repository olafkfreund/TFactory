object Calc {
    fun add(a: Int, b: Int): Int = a + b

    fun divide(a: Int, b: Int): Int {
        require(b != 0) { "division by zero" }
        return a / b
    }
}
