---
name: knowledge-base
title: Knowledge Base
summary: When and how to create, validate, and maintain repo-local knowledge base articles for curated explanations of dgov concepts, architecture, and operations.
applies_to: [knowledge, kb, docs/knowledge, obsidian, traversal, graph, documentation]
priority: must
---
## When
- a task involves editing, adding, or restructuring docs/knowledge/ articles
- a worker needs to explain an architecture decision already recorded in canonical repo state
- a project wants to surface source-backed conventions through Obsidian or dgov kb commands
- a task prompt references a knowledge base article during the Orient phase
- traversal, related edges, or graph queries are needed to navigate between articles

## Do
- treat KB articles as source-backed explanations, not as ledger/governor/SOP authority
- point `sources` to canonical repo-relative files outside `docs/knowledge/`
- include valid, intentional `related` edges that help workers traverse between articles
- run `uv run dgov kb validate` after any KB edit before committing
- keep frontmatter strict with all required fields: `id`, `title`, `kind`, `status`, `sources`, `related`
- use `kind` values from the allowed set: `architecture`, `concept`, `index`, `operation`
- use `status` values from the allowed set: `draft`, `living`, `stable`
- write article body as plain Markdown with the first `#` heading matching the `title`
- use `.dgov/sops/` for worker execution guidance and `dgov ledger` for durable decisions
- promote new durable rules, decisions, bugs, or debt to the ledger or governing policy before explaining them in the KB

## Do Not
- cite `docs/knowledge/` articles as self-authoritative sources
- leave `sources` empty or point them inside `docs/knowledge/`
- add `related` edges that do not serve a real traversal purpose
- introduce typed edge fields in frontmatter until real traversal queries justify them
- commit `.obsidian/` workspace state, personal plugins, themes, or layouts
- use the KB as a second ledger; durable rules and decisions belong in `dgov ledger`
- embed worker execution guidance in KB articles when a local SOP would suffice

## Verify
- run `uv run dgov kb validate` and confirm zero issues before finishing
- run `uv run dgov kb graph` to inspect nodes and edges after structural changes
- run `uv run dgov kb related <id>` to test traversal from new or changed articles
- confirm every article has at least one source entry outside `docs/knowledge/`
- confirm `.gitignore` ignores `.obsidian/` so workspace state is never committed
- check that the first `#` heading in the article body matches the frontmatter `title`

## Escalate
- if a project needs a new `kind` or `status` value that does not exist in the schema
- if multiple projects need the same custom edge type; that is usually a core dgov schema change
- if a KB article starts making policy or authority claims that belong in `.dgov/governor.md`
- if the knowledge base should integrate with external documentation systems or CI gates
