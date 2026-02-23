## cleanup
Improve code quality of module <>:
Remove all unused, unnecessary, or obsolete code.
Identify and refactor duplicate logic into a single reusable function or class.
Make sure files are not bigger than 1000 lines. Create <name>_helpers and add pytests if not existing for the helpers. Check if other modules can use these helpers.
Ensure the refactored code follows Python best practices (PEP 8, readability, maintainability).
Add comprehensive unit tests using pytest for the common/refactored code, including edge cases.

## PR
Please do a PR review and return an md file

## Branches
Cleanup all local branches except main and the open branch