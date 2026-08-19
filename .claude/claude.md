## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them — don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, read the spec documents first. If still unclear, stop, name what's confusing, and ask.
If mid-task you discover the problem is larger or different than expected, surface the scope change immediately. Don't quietly expand.

For destructive or irreversible operations (deleting data, dropping collections, overwriting files), confirm with the user before executing — even if the request seems clear.

---

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No defensive error handling for inputs the caller guarantees won't occur.
If you write 200 lines and it could be 50, rewrite it.
Never add a new dependency for what a few lines can already do.

The ladder — stop at the first rung that holds: does this need to exist at all (YAGNI) → reuse something already in this codebase → stdlib → native platform feature → an already-installed dependency → one line → only then, the minimum new code.

If a simplification cuts a real corner (global lock, O(n²) scan, naive heuristic), mark it with a comment naming the ceiling and the upgrade path, e.g. # simplification: global lock, switch to per-key locks if throughput matters.

This is about structure, not correctness — never simplify away input validation at trust boundaries, error handling that prevents data loss, security checks, or anything explicitly requested.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

---

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
Remove imports/variables/functions that **your** changes made unused.
Don't remove pre-existing dead code unless asked.

When fixing a bug: fix the root cause, not the symptom. Check callers of the function being touched — a guard added once in the shared function is usually a smaller, more correct diff than patching the one calling path the report happened to name.

The test: every changed line should trace directly to the user's request.

---

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan before starting:
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
Non-trivial logic (a branch, a loop, a parser, a money/security path) should leave one minimal runnable check behind — an assert-based sanity check or one small test — even outside a larger multi-step task. Trivial one-liners don't need this.

Loop autonomously when success criteria are clear. Pause and check in when a mid-task discovery changes the shape of the problem.