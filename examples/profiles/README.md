# Example profiles

These profiles are **examples and test fixtures only**. They are:

- not optimized;
- not benchmarked;
- not approved for any production use;
- not a claim that one model family or skill variant is better than another.

They exist to exercise the M3 lifecycle (`validate → resolve → lock →
compile → run → export → verify`) and to show meaningful differences between
workflows:

| Profile | Workflow | Tool policy | Notes |
|---|---|---|---|
| `coding.yaml` | coding | `coding-tools` (writes allowed) | canonical skills + qwen variant selector |
| `debugging.yaml` | debugging | `coding-tools` | reproduce → diagnose → repair |
| `pr-review.yaml` | pr_review | `review-tools` (read-only) | findings only, cannot write |
| `ci-repair.yaml` | ci_repair | `coding-tools` (controlled writes) | must not disable checks (verified in M4) |
| `security-review.yaml` | security_review | `security-tools` (read-only, no scanning) | authorized defensive analysis |

Model-family variant metadata for DeepSeek/Qwen/GLM shows how selectors work;
it encodes no performance ranking.

Import components and lock a profile with:

```bash
hop registry import components
hop profile explain examples/profiles/debugging.yaml
```
