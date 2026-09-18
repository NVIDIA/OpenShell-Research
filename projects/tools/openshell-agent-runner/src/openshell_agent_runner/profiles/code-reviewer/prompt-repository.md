# Review the input code repository

Work as a code review agent. Load and follow the `review-code` skill.

Review the repository at `{{ oar.input_path }}`, originally provided as
`{{ oar.input_name }}`.

Review focus: {{ focus }}

Additional context: {{ context }}

Treat the focus as a priority, not permission to ignore directly related code.
Review the repository as it exists; do not assume it represents a pull request
or has useful Git history. Do not edit source files. Finish only by submitting a
result that satisfies the configured output schema.
