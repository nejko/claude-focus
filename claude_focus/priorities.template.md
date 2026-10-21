# Priority rules for claude-focus

The scorer reads this file for every session. Edit it at any time; new scores
use the new text. Short, clear rules work best. This is a starting point:
replace the examples with your own.

## Always high (80-100)

- Security problems: live data exposure, open endpoints, leaked keys.
- Production broken for customers.
- Legal, tax or filing deadlines within 3 days.
- A customer-facing message that is ready and only waits for my send.
- Another person is blocked until I act (a colleague, a customer, an accountant).

## Medium (40-79)

- Decisions that unblock a Claude session's multi-step work.
- Commitments to a customer with a date this week.
- Trials, subscriptions or keys that expire within 7 days.
- Launches and internal deadlines.

## Low (1-39)

- Reports with no action for me.
- Optional improvements and ideas.
- Personal admin with no deadline.

## My work (EDIT: most important first)

- Example: Acme (my company; its customers come first)
- Example: an open source project
- Example: personal matters
