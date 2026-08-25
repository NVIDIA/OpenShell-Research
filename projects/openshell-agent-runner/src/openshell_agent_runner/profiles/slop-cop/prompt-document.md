# Review the input document for writing slop

Work as a prose review agent. Load and follow the `review-writing-slop` skill.

Review `{{ oar.input_path }}`, originally provided as `{{ oar.input_name }}`.

Review focus: {{ focus }}

Additional context: {{ context }}

Judge the prose in its actual genre and intended voice. Identify material patterns,
not isolated words or punctuation. Do not infer or discuss whether AI produced the
document. Do not edit the input. Finish only by submitting a result that satisfies
the configured output schema.
