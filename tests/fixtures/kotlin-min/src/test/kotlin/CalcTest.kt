import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith

class CalcTest {
    @Test
    fun addsTwoNumbers() {
        assertEquals(5, Calc.add(2, 3))
    }

    @Test
    fun dividesEvenly() {
        assertEquals(4, Calc.divide(12, 3))
    }

    @Test
    fun rejectsDivisionByZero() {
        assertFailsWith<IllegalArgumentException> { Calc.divide(1, 0) }
    }
}
