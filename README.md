# ops-ai-recipes

Small, complete automations for operations teams. One narrow task per recipe: the problem, the inputs, the guardrails, working code, and a test that proves it.

Most recipes use AI where it earns its place. Some don't use it at all, because the job is deterministic and a model would only add a way to be wrong. Each recipe says which, and why.

| # | Recipe | Uses AI | Stack |
| --- | --- | --- | --- |
| 01 | [Find the SSRS reports nobody runs](recipes/01-dead-ssrs-reports/) | No, on purpose | T-SQL |

## Rules every recipe follows

- **Read-only by default.** If a recipe changes anything, it says so first and asks.
- **Refuses instead of guessing** when the data can't support an answer.
- **Never reads what it doesn't need:** no credentials, no business rows unless the task requires them.
- **Tested.** Every recipe ships with a test against synthetic data, and says plainly what it hasn't been run on.

## Who this is for

Ops and reporting teams who know their systems and want the boring, risky parts automated without trusting a black box. Built by [Tom Pinder](https://startvest.ai/tom-pinder), who spent fifteen years running the systems, integrations, and reporting that operations teams depend on.

I write about the why behind each recipe at [startvest.ai/blog](https://startvest.ai/blog).

## License

MIT
