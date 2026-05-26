---
name: state-modeling-review
title: State Modeling Review
summary: Flag state bloat, sentinel values, grab-bag models, and mutation ambiguity in reviewed code.
applies_to: [state, model, dataclass, pydantic, types, review, typed, sentinel, optional, frozen, getattr, enum]
priority: must
---
## When
- reviewing changes to data models, state types, or lifecycle code
- reviewing code that adds boolean flags, optional fields, or status strings

## Do
- flag sentinel values (empty string, zero, "none") where None would express absence
- flag optional-field grab-bags where a discriminated union would make wrong states impossible
- flag functions that mutate their input AND return it (pick one: mutate and return None, or clone and return new)
- flag if-chains where every branch returns the same shape (convert to a data table)
- flag dead type variants that are never constructed
- flag stored state that could be derived from existing data or events

## Do Not
- demand refactoring beyond the task scope
- flag working patterns just because they are not ideal if the diff did not introduce them
- flag genuine state machines as "stored state" (a checkout step IS the state, not a cached conclusion)

## Verify
- check whether flagged patterns are new in this diff or pre-existing
- confirm the suggested alternative actually fits the domain before recommending it

## Escalate
- if state model issues are systemic (multiple models affected across the codebase)
- if fixing the model would change persistence semantics or public interfaces
