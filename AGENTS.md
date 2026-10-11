# AGENTS.md -- development guide for AI coding agents

## Before Pushing

* Update affected docs and comments in the code
* Always run all the tests, formatting, and lint and fix issues

## Commit messages

* Include a short validation summary in the commit body.

## Coding Rules

* Keep things DRY.
* Always ask before pulling in an external library like Boost.

## Backward compatibility

* Until the language moves past 0.1, we do NOT have to maintain backward
  compatibility. We will change the language as we learn and fix the in-repo
  examples to match, but we will not maintain support for deprecated keywords
  and language features.
