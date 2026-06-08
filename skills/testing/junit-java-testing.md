# junit-java-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: java,junit5,jupiter,assertj,mockito,jacoco,pit,mutation,maven,gradle,parameterized

---

# JUnit 5 Java Testing

Use this skill when writing or reviewing TFactory Java-lane tests with JUnit 5 (Jupiter) — `@Test`, `@Nested`, `@ParameterizedTest`, lifecycle hooks — fluent assertions with AssertJ `assertThat`, mocking collaborators with Mockito `@Mock`/`@InjectMocks`, measuring line/branch coverage with JaCoCo, and proving the tests actually catch bugs with PIT mutation testing, all wired through Maven or Gradle as the build for TFactory's Java wedge.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# JUnit 5 Java Testing

TFactory's Java wedge generates JUnit 5 (Jupiter) tests, runs them under Maven or Gradle, measures coverage with **JaCoCo**, and routes mutation testing through **PIT** (the Java arm of the mutation lane, dispatched alongside Python `mutate_probe` and TypeScript Stryker). This skill covers idiomatic Jupiter tests, AssertJ fluent assertions, Mockito for collaborators, and the JaCoCo+PIT signals that gate quality.

---

## When to use this skill
- Generating or reviewing JUnit 5 tests for Java code on the feature branch (Java lane: JUnit5 + JaCoCo + PIT).
- Writing parameterized tests, nested grouping, and lifecycle setup with Jupiter.
- Asserting with AssertJ's fluent `assertThat` instead of raw JUnit assertions.
- Mocking collaborators with Mockito (`@Mock`, `@InjectMocks`, `verify`, argument captors).
- Configuring JaCoCo coverage and PIT mutation thresholds in Maven/Gradle.
- Do NOT trigger for: Python (`pytest`) or TypeScript (`Jest`) lanes, browser flows (Playwright/browser lane), HTTP contract tests (api lane), or spinning real deps in containers (integration lane — though JUnit + Testcontainers-Java is a valid combination).

---

## Key principles
1. **One behavior per test, named for the behavior** — a `@Test` method's name should read like a spec sentence (`returnsEmptyWhenNoMatch`). When it fails, the name tells you what broke without reading the body.
2. **Arrange-Act-Assert, visibly** — separate setup, the single call under test, and the assertions. AAA makes intent obvious and keeps tests from drifting into multi-behavior blobs.
3. **AssertJ over raw asserts** — `assertThat(x).isEqualTo(y)` reads naturally, chains, and produces far better failure messages than `assertEquals`. Use it consistently.
4. **Mock collaborators, not the unit under test** — mock the dependencies the unit *calls* (a repository, a clock, an HTTP client); never mock the class you're testing.
5. **Coverage shows what ran; mutation shows what's tested** — 100% JaCoCo coverage with assertion-free tests catches nothing. PIT mutates the code and checks your tests *fail* — that's the real signal.
6. **Deterministic by construction** — inject `Clock`, seed randomness, avoid real time/IO. Flaky Java tests usually come from `Instant.now()`, `HashMap`/`HashSet` iteration order, or shared static state.
7. **Test boundaries and the error contract** — exceptions are behavior. Use `assertThatThrownBy`/`assertThrows` to pin which exception, message and cause a method throws, not just the happy path.

---

## Core concepts
**JUnit 5 (Jupiter)** — the modern API: `org.junit.jupiter.api.*`. `@Test`, `@BeforeEach`/`@AfterEach`, `@BeforeAll`/`@AfterAll` (static unless `@TestInstance(PER_CLASS)`), `@DisplayName`, `@Disabled`, and the `@ExtendWith` extension model that replaces JUnit 4 runners/rules.

**@Nested** — an inner non-static `@Nested` class groups tests for one scenario or method, with its own `@BeforeEach`. Produces a readable tree: `OrderService > whenStockAvailable > reservesItems`.

**@ParameterizedTest** — runs one test body across many inputs via `@ValueSource`, `@CsvSource`, `@MethodSource`, or `@EnumSource` — collapsing N near-identical tests into one.

**AssertJ** — fluent assertion library: `assertThat(actual).isEqualTo(...)`, `.contains(...)`, `.hasSize(...)`, `.extracting(...)`, `assertThatThrownBy(...)`. Chainable, type-aware, rich diffs.

**Mockito** — mocking framework. `@Mock` creates a mock, `@InjectMocks` builds the unit under test with mocks injected, `when(...).thenReturn(...)` stubs, `verify(...)` asserts interactions, `ArgumentCaptor` captures passed args. Enabled via `@ExtendWith(MockitoExtension.class)`.

**JaCoCo** — bytecode coverage agent producing line/branch coverage reports; can fail the build below a threshold via a `check` rule.

**PIT (pitest)** — mutation testing: it injects bugs (negate conditionals, swap math, void method calls) and checks your suite kills them. Mutation score = killed / total; the real "are these tests worth anything" metric. This is TFactory's Java mutation signal.

---

## Common tasks

### A focused unit test with lifecycle + AssertJ
Name for behavior; arrange/act/assert; fluent assertions.

```java
import org.junit.jupiter.api.*;
import static org.assertj.core.api.Assertions.*;

class PriceCalculatorTest {

    PriceCalculator calc;

    @BeforeEach
    void setUp() {
        calc = new PriceCalculator();
    }

    @Test
    @DisplayName("applies a 10% discount above the threshold")
    void appliesDiscountAboveThreshold() {
        // arrange
        var cart = new Cart(1500);  // cents
        // act
        int total = calc.totalWithDiscount(cart);
        // assert
        assertThat(total).isEqualTo(1350);
    }

    @Test
    void rejectsNegativeAmounts() {
        assertThatThrownBy(() -> calc.totalWithDiscount(new Cart(-1)))
            .isInstanceOf(IllegalArgumentException.class)
            .hasMessageContaining("non-negative");
    }
}
```

### Group scenarios with @Nested
A readable tree, each branch with its own setup.

```java
class OrderServiceTest {

    @Nested
    class WhenStockAvailable {
        @BeforeEach void seedStock() { /* ... */ }

        @Test void reservesItems() { /* ... */ }
        @Test void emitsReservedEvent() { /* ... */ }
    }

    @Nested
    class WhenOutOfStock {
        @Test void rejectsWithBackorder() { /* ... */ }
    }
}
```

### Parameterize over inputs
Collapse many similar cases; cover boundaries cheaply.

```java
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.*;

class DiscountTest {

    @ParameterizedTest(name = "amount {0} cents -> {1} cents")
    @CsvSource({
        "999,  999",    // below threshold: no discount
        "1000, 900",    // exactly at threshold
        "2000, 1800",
    })
    void discountByAmount(int amount, int expected) {
        assertThat(new PriceCalculator().totalWithDiscount(new Cart(amount)))
            .isEqualTo(expected);
    }

    @ParameterizedTest
    @EnumSource(Tier.class)
    void everyTierHasARate(Tier tier) {
        assertThat(tier.rate()).isBetween(0.0, 1.0);
    }
}
```

### Mock collaborators with Mockito
Mock dependencies, inject into the unit, verify interactions.

```java
import org.junit.jupiter.api.*;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.*;
import org.mockito.junit.jupiter.MockitoExtension;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class CheckoutServiceTest {

    @Mock OrderRepository repo;        // collaborator
    @Mock PaymentGateway gateway;      // collaborator
    @InjectMocks CheckoutService service;   // unit under test, mocks injected

    @Test
    void chargesThenPersists() {
        when(gateway.charge(1500)).thenReturn("pay_123");

        service.checkout(new Cart(1500));

        // Verify the interaction contract.
        var saved = ArgumentCaptor.forClass(Order.class);
        verify(repo).save(saved.capture());
        assertThat(saved.getValue().paymentRef()).isEqualTo("pay_123");
        verify(gateway).charge(1500);
        verifyNoMoreInteractions(gateway);
    }

    @Test
    void doesNotPersistWhenChargeFails() {
        when(gateway.charge(anyInt())).thenThrow(new PaymentException("declined"));

        assertThatThrownBy(() -> service.checkout(new Cart(1500)))
            .isInstanceOf(PaymentException.class);

        verify(repo, never()).save(any());   // no order on failure
    }
}
```

### Inject a fixed Clock for deterministic time
Never call `Instant.now()` in tested code — inject a `Clock`.

```java
import java.time.*;

@Test
void stampsCreatedAt() {
    Clock fixed = Clock.fixed(Instant.parse("2026-06-06T00:00:00Z"), ZoneOffset.UTC);
    var service = new OrderService(repo, fixed);

    var order = service.create(new Cart(1500));

    assertThat(order.createdAt()).isEqualTo(Instant.parse("2026-06-06T00:00:00Z"));
}
```

### Wire JaCoCo + PIT in Maven
Coverage report + a mutation threshold that fails the build.

```xml
<!-- pom.xml plugins -->
<plugin>
  <groupId>org.jacoco</groupId>
  <artifactId>jacoco-maven-plugin</artifactId>
  <version>0.8.12</version>
  <executions>
    <execution><goals><goal>prepare-agent</goal></goals></execution>
    <execution><id>report</id><phase>test</phase><goals><goal>report</goal></goals></execution>
  </executions>
</plugin>
<plugin>
  <groupId>org.pitest</groupId>
  <artifactId>pitest-maven</artifactId>
  <version>1.17.0</version>
  <dependencies>
    <dependency>  <!-- JUnit 5 support for PIT -->
      <groupId>org.pitest</groupId>
      <artifactId>pitest-junit5-plugin</artifactId>
      <version>1.2.1</version>
    </dependency>
  </dependencies>
  <configuration>
    <mutationThreshold>80</mutationThreshold>   <!-- fail under 80% killed -->
    <targetClasses><param>com.acme.checkout.*</param></targetClasses>
  </configuration>
</plugin>
```

```bash
mvn test                 # runs JUnit 5 + JaCoCo report -> target/site/jacoco/
mvn org.pitest:pitest-maven:mutationCoverage   # PIT report -> target/pit-reports/
```

### The Gradle equivalent
```kotlin
// build.gradle.kts
plugins {
    java
    jacoco
    id("info.solidsoft.pitest") version "1.15.0"
}
tasks.test { useJUnitPlatform() }          // JUnit 5 engine
tasks.jacocoTestReport { dependsOn(tasks.test) }
pitest {
    junit5PluginVersion.set("1.2.1")
    mutationThreshold.set(80)
    targetClasses.set(setOf("com.acme.checkout.*"))
}
```

---

## Gotchas
1. **JUnit 4 imports silently disable tests** — mixing `org.junit.Test` (JUnit 4) into a Jupiter project means those methods just don't run. Always import `org.junit.jupiter.api.Test`; check the build actually reports the test count.
2. **`@BeforeAll`/`@AfterAll` must be static** — unless the class is `@TestInstance(Lifecycle.PER_CLASS)`. A non-static `@BeforeAll` fails to initialize and your shared setup never happens.
3. **PIT reports "no tests" with JUnit 5** — PIT needs the `pitest-junit5-plugin` dependency. Without it, mutation coverage is 0% and the signal is meaningless. Add the plugin to the PIT config.
4. **High JaCoCo, low PIT score = assertion-free tests** — tests that call code but assert nothing inflate coverage while killing no mutants. The mutation score exposes them; trust PIT over line coverage.
5. **Mockito `UnnecessaryStubbingException`** — strict stubs (the default with `MockitoExtension`) fail the test if a `when(...)` is never used. Remove unused stubs or use `lenient()` deliberately, not blanket-leniently.
6. **`HashMap`/`HashSet` iteration order** — asserting on the order of a hashed collection is non-deterministic across JVMs/runs and will flake under 3× stability. Assert with `containsExactlyInAnyOrder` or sort first.
7. **Forgetting `useJUnitPlatform()` in Gradle** — without it, Gradle runs the old JUnit 4 engine and skips every Jupiter test, reporting green while testing nothing.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Tests with no assertions (just call the method) | Inflate JaCoCo coverage but kill zero PIT mutants — catch nothing | Assert the outcome with AssertJ; let PIT confirm the assertions bite |
| Mocking the class under test | You're testing the mock, not the code; refactors can't break it | Mock collaborators only; instantiate the real unit under test |
| `assertEquals` everywhere with no message | Poor failure diagnostics, awkward for collections/exceptions | Use AssertJ `assertThat(...)` chains with rich built-in diffs |
| One `@Test` exercising many behaviors | A failure can't tell you which behavior broke; brittle | One behavior per test, named for the behavior, AAA structure |
| `Instant.now()` / `new Random()` in tested code | Non-deterministic; flakes under 3× stability re-runs | Inject `Clock`/seeded `Random`; pin them in tests |
| Asserting on `HashMap`/`HashSet` order | Iteration order isn't guaranteed → intermittent failures | `containsExactlyInAnyOrder` or sort before asserting |
| Chasing 100% line coverage, ignoring mutation score | Coverage measures execution, not test strength | Set a PIT `mutationThreshold` and treat surviving mutants as gaps |
| Leftover `when(...)` stubs that are never called | Strict Mockito fails; signals a test that doesn't do what it claims | Delete unused stubs; the failure is telling you the test is wrong |
