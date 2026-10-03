import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class CalcTest {
    @Test
    void addsTwoNumbers() {
        assertEquals(5, Calc.add(2, 3));
    }

    @Test
    void dividesEvenly() {
        assertEquals(4, Calc.divide(8, 2));
    }

    @Test
    void rejectsZeroDivisor() {
        IllegalArgumentException e =
                assertThrows(IllegalArgumentException.class, () -> Calc.divide(1, 0));
        assertEquals("division by zero", e.getMessage());
    }
}
