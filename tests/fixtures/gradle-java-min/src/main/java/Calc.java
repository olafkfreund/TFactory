/** Subject under test for the Gradle-built Java verify lane (Factory#3151). */
public final class Calc {
    private Calc() {}

    public static int add(int a, int b) {
        return a + b;
    }

    public static int divide(int a, int b) {
        if (b == 0) {
            throw new IllegalArgumentException("division by zero");
        }
        return a / b;
    }
}
