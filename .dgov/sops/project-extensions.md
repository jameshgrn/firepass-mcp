---
name: project-extensions
title: Project-Local Extensions
summary: When and how to put target-repo build, verify, runtime, data, CI, and convention policy in project-local surfaces instead of core dgov or repeated task prompts.
applies_to: [project.toml, verify, recipe, sop, extension, local, convention, build, runtime, ci, data, secrets]
priority: must
---
## When
- a project uses the same verification command in multiple task prompts
- a project has custom setup, lint, test, or coverage steps that repeat across plans
- a team wants to change a verify command once and have it apply to all future tasks
- a task depends on a language, platform, application runtime, database, fixture,
  simulator, external service, CI provider, or local convention that is not
  universal to dgov itself
- a worker failure reveals missing repo setup rather than a core dgov execution
  bug

## Do
- move repeated verification commands into `[verify.<name>]` recipes in `.dgov/project.toml`
- create project-local SOPs in `.dgov/sops/` for conventions that are specific to this repo
- put repo-owned setup wrappers in repo scripts or `.dgov/project.toml` recipes,
  then have dgov call those stable surfaces
- reference the verify recipe by name in task prompts instead of pasting the full command
- use a `.dgov/project.toml` recipe shape like `[verify.test]` with
  `command = "uv run pytest -q -m unit {test_dir}"` and
  `description = "Run targeted unit tests"`
- use a `.dgov/project.toml` recipe shape like `[verify.app]` with
  `command = "./scripts/verify-app.sh {target}"` and
  `description = "Run the repo-owned app verification wrapper"`

## Do Not
- paste the same long command string into every task prompt
- embed project-specific conventions in ad hoc prompt text when a local SOP would suffice
- treat `.dgov/project.toml` as a static template; update it when verification needs change
- add target-project wrappers to core dgov just because one repo needs them
- hide secrets, private data paths, or environment assumptions in worker prompts

## Verify
- confirm that `dgov verify run <recipe-name>` works before committing the plan
- check that `.dgov/sops/` guidance is discoverable by workers during the `Orient` phase
- ensure verify recipes are referenced by name rather than inlined in prompt text
- run the project-owned wrapper directly when diagnosing whether the wrapper or
  dgov invocation is failing
- confirm `.gitignore` keeps durable governance artifacts trackable and only
  ignores runtime state, caches, and generated outputs

## Escalate
- if a verify recipe needs to vary per task and a single `project.toml` entry is insufficient
- if multiple projects need the same hook shape but different commands; that is
  usually a core dgov hook, not a core hardcoded command
- if the project-local extension would duplicate a core SOP; prefer updating the core SOP instead
- if the project policy changes public interfaces, data models, schema, CI
  gates, secret handling, or architecture boundaries; ask before proceeding
