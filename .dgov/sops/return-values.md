---
name: return-values
title: Assign Return Values at API Boundaries
summary: Prevent silent no-ops from unassigned getter, factory, and builder calls.
applies_to: [python, javascript, typescript, api, plugin, return, interface, contract]
priority: must
---
## When
- calling methods that return a value you need (getters, factories, builders, parsers)
- working with unfamiliar APIs or libraries where return types are not obvious
- chaining operations where an intermediate result must be captured

## Do
- always assign the return value of `.get()`, `.create()`, `.build()`, `.parse()`,
  `.copy()`, `.replace()`, `.with_*()`, and similar factory/transform methods
- verify that the variable you assign to is actually used downstream
- check the API docs or source for whether a method mutates in-place vs returns a new value
- when calling a method on an external object, confirm the method exists on that type

## Do Not
- call a getter or factory method as a standalone expression (bare `self.x.get(...)` on its own line)
- assume a method mutates the receiver; many APIs return a new object instead
- call methods that do not exist on the receiver type (e.g. `.grab()` on a class without it)
- use `result.model_dump()` on a plain dict or `obj.engine()` on a class that lacks it

## Verify
- search your diff for bare method calls on their own line that are not `print`, `logger.*`,
  `.append()`, `.extend()`, `.add()`, `.remove()`, `.close()`, `.emit()`, or similar void methods
- for every assignment from an external API call, confirm the method exists and returns what you expect
- if a parameter is set via a builder pattern, confirm the final object reflects the change

## Escalate
- if the API documentation is ambiguous about return type vs in-place mutation
- if the method you need does not exist on the target class
