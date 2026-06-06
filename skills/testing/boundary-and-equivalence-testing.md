# boundary-and-equivalence-testing

> Source: TFactory (internal) | v0.1.0 | License: MIT OR GPL-3.0 | Tags: boundary-value-analysis,equivalence-partitioning,decision-tables,pairwise,combinatorial,off-by-one,edge-cases

---

# Boundary and Equivalence Testing: Maximum Coverage, Minimum Cases

Use this skill when picking *which input values* to test — equivalence partitioning (grouping inputs that behave the same), boundary value analysis (testing the edges where bugs cluster), decision tables (mapping combinations of conditions to expected outcomes), and pairwise/combinatorial reduction (covering interactions without exploding the case count). Triggers: a function takes a numeric/string/date input and you must choose representative values; off-by-one or fencepost bugs are suspected; an AC has several boolean conditions and you need a systematic table of cases; a test matrix has too many combinations to run exhaustively; the `mutation` lane shows a surviving mutant at a comparison operator (`<` vs `<=`); you want the fewest tests that still kill the most mutants. This skill produces the minimal high-signal case set TFactory's Evaluator rewards.

---

When this skill is activated, always start your first response with the 🧢 emoji.

# Boundary and Equivalence Testing: Maximum Coverage, Minimum Cases

Bugs cluster at boundaries and at unanticipated combinations of conditions. Exhaustive testing is impossible; random testing is wasteful. Equivalence partitioning, boundary value analysis, decision tables, and pairwise reduction are the formal techniques for choosing the *smallest set of inputs that exercises the most distinct behaviour*. In TFactory terms, these techniques produce tests that lift `coverage_delta` and, crucially, *kill mutants* — because boundary tests are exactly what catches a flipped comparison operator.

---

## When to use this skill
- Choosing representative input values for a numeric / string / date / enum parameter.
- Hunting off-by-one / fencepost bugs at range edges.
- An AC has multiple boolean conditions and needs a systematic outcome table.
- A combination matrix is too large to run exhaustively (you need pairwise reduction).
- The `mutation` lane reports a SURVIVED mutant at a `<`/`<=`/`>`/`>=`/`==` operator.
- You want the fewest tests that maximise mutation-kill, not just line coverage.

Do NOT trigger for:
- Building the actual fixture/factory data — see `test-data-and-fixtures` (this skill selects *which values*; that one *constructs* them).
- Choosing lanes/coverage targets — see `test-strategy`.
- Turning ACs into phases — see `acceptance-criteria-testing`.

---

## Key principles
1. **One value per partition, plus every boundary.** If inputs in a range behave identically, one representative suffices — but always test both edges of each partition, because that's where the bugs are.
2. **Boundaries are where mutants live.** A flipped `<` → `<=` only fails at the exact edge value. Boundary tests are the cheapest way to kill comparison-operator mutants the Evaluator's `mutate-and-check` introduces.
3. **Test the value *at*, *just below*, and *just above* each boundary.** The classic 3-point (or 2-point min/min+1) probe catches off-by-one in both directions.
4. **Invalid partitions deserve cases too.** Negative, zero, empty, null, over-max, malformed — error paths are partitions, and ACs with "shall reject" clauses verify them.
5. **Decision tables make combinations exhaustive and explicit.** When N booleans interact, a table of condition-combinations → expected-action prevents missed cells and reads as a spec.
6. **Pairwise covers most interaction bugs at a fraction of the cost.** Most defects involve at most two factors interacting; pairwise (all-pairs) covers every pair with far fewer rows than the full cross-product.
7. **Don't test what can't vary.** A parameter constrained to one value by the type system or an upstream guard isn't a partition — don't waste a case on it.
8. **Name the partition in the test.** `test_quantity_at_max`, `test_quantity_over_max` — the LLM `semantic_relevance` signal and human reviewers both read the name as the rationale.

---

## Core concepts
**Equivalence partitioning (EP)** — Divide the input domain into classes where every member is treated the same by the code (e.g. age: <0 invalid, 0–17 minor, 18–64 adult, 65+ senior). Test one representative per class.

**Boundary value analysis (BVA)** — For each partition edge, test the boundary and its immediate neighbours. For `0–17`: test -1, 0, 17, 18. Edges are shared between adjacent partitions, so they double as partition reps.

**Decision table** — Rows = combinations of conditions (true/false), columns = the resulting action/outcome. Each rule (row) becomes a test. Forces you to enumerate every combination and spot impossible/contradictory ones.

**Pairwise / combinatorial (all-pairs)** — Instead of the full N-factor cross-product, generate the minimal set of cases such that every *pair* of factor-values appears together at least once. Cuts e.g. 3⁴=81 combinations to ~9 while covering all pairwise interactions.

**Off-by-one / fencepost** — The error class BVA targets: `<=` vs `<`, `len` vs `len-1`, inclusive vs exclusive range ends.

**Mutation correlation** — TFactory's `mutate_probe` flips operators and constants. Boundary + decision-table tests are precisely the ones that flip from pass→fail when a mutant is introduced (KILLED), which is what makes them high-confidence in the verdict.

---

## Common tasks
### Partition + boundary a numeric input
For a discount valid on quantities 10–99:
```python
@pytest.mark.parametrize("qty,discounted", [
    (9,  False),  # just below lower boundary
    (10, True),   # lower boundary
    (11, True),   # just inside
    (99, True),   # upper boundary
    (100, False), # just above upper boundary
])
def test_bulk_discount_boundaries(qty, discounted):
    assert applies_bulk_discount(qty) is discounted
```
This 5-case set kills `<`/`<=` and `>`/`>=` mutants on both ends.

### Build a decision table
Eligibility = member AND (in_stock OR backorder_allowed):
| member | in_stock | backorder | → can_order |
|---|---|---|---|
| T | T | - | T |
| T | F | T | T |
| T | F | F | F |
| F | - | - | F |
Each row → one test. The `F | - | -` row collapses three combinations because `member=F` short-circuits.

### Reduce a combinatorial explosion with pairwise
Factors: payment {card, paypal, crypto} × region {US, EU, APAC} × tier {free, pro} × device {web, mobile} = 36 combos. Generate an all-pairs set (~9 rows) so every payment×region, payment×tier, region×device … pair appears once. Use a pairwise generator (e.g. `allpairspy`) to produce the rows, then parametrise.

### Cover invalid partitions
```python
@pytest.mark.parametrize("bad", ["", "   ", None, "x"*256])  # empty, blank, null, over-max
def test_username_rejected(bad):
    with pytest.raises(ValidationError):
        validate_username(bad)
```

---

## Gotchas
1. **Testing the middle, skipping the edges.** A test at qty=50 passes whether the bound is `>=10` or `>10`. Only the boundary values distinguish them — and only they kill the operator mutant.
2. **Off-by-one in the *test*.** Asserting `100` is valid when the real max is `99` bakes the bug into the test. Re-derive boundaries from the AC, not from the implementation.
3. **Full cross-product when pairwise suffices.** 81 cases where 9 cover every interaction — slow in the sandbox, and the extra 72 mostly duplicate behaviour (dedup targets).
4. **Forgetting invalid/empty partitions.** Happy-path-only tests leave error paths uncovered; the AC's "shall reject" clause goes unverified.
5. **Decision table with contradictory/impossible rules.** Listing a row that can't occur (e.g. `in_stock=T AND sold_out=T`) wastes a test and confuses the reader. Prune impossible combinations.
6. **Treating an enum as a continuous range.** Enums have no "boundary" — test each value, not edges between them.
7. **Boundary tests with weak assertions.** A boundary case that asserts only "didn't raise" still lets the mutant survive. Pair BVA with strong assertions — see `assertion-design`.

---

## Anti-patterns / common mistakes
| Mistake | Why it's wrong | What to do instead |
|---|---|---|
| Only testing typical/middle values | Comparison-operator mutants survive; off-by-one ships | Test at, just-below, and just-above every boundary |
| Deriving boundaries from the code | Bakes the implementation's off-by-one into the test | Derive boundaries from the AC/spec, independently |
| Full N-factor cross-product | Slow in sandbox; mostly duplicate behaviour → dedup churn | Pairwise/all-pairs covers interaction bugs in far fewer cases |
| Omitting invalid/empty/null partitions | Error paths and "shall reject" clauses go untested | Treat each invalid class as a partition with its own case |
| Decision table missing combinations | Untested condition cells = silent gaps | Enumerate all condition combinations; prune only impossible ones |
| Boundary edges as magic numbers | Reviewer/LLM can't tell which edge is exercised | Name/label each case (lower, lower-1, upper, upper+1) |
| BVA case with "did not throw" assertion | Mutant survives despite hitting the boundary | Assert the concrete behaviour at the boundary |
| Edge-testing an enum/categorical | Enums have discrete values, not boundaries | Test each enum value as its own partition |
