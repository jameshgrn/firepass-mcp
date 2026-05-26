---
name: large-file-handling
title: Large File Handling
summary: Use targeted reads and surgical edits on large files to preserve context window and diff clarity.
applies_to: [python, javascript, typescript, large-file, chunk, binary]
priority: must
---
## When
- reading or editing files over ~200 lines
- working with data files, configs, or generated code

## Do
- use file_symbols first to locate the target function or class
- use read_file with line ranges for focused context
- use head, tail, or word_count to gauge size before full reads
- use edit_file or apply_patch for surgical changes

## Do Not
- read an entire large file when you only need one function
- use write_file on files larger than 100 lines when edit_file suffices
- read the same large file multiple times without narrowing the range

## Verify
- confirm your edit did not introduce unintended changes in untouched sections
- check that git_diff shows only the intended hunks

## Escalate
- if the task requires understanding the full file structure (request file_symbols output first)
