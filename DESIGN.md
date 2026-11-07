# Data Challenge DEV viewer
A local reading tool for understanding the actual conversation types in the organizer dataset. The main question is: what do client, bot and operator conversations look like?

Two reading panes: a searchable list and the transcript; a compact facts rail provides provenance and speaker counts. The green-grey frame belongs to Freedom's material; white reading surfaces and a pale green operator bubble let the transcript dominate. Manrope is served locally. No decorative motion.

Tokens: frame #edf3ef, paper #ffffff, transcript #f7f9f8, ink #172c24, secondary #586b62, green #117047, operator #e4f2e9, rule #dce5df. All counts derive from dataset.json. Mobile uses list/detail navigation with a back button.

The three sections are Dialogues, ChatGPT labels and Claude labels. Both label views share one layout and preserve the conversation when switching. The current source controls the main score and explanations; a secondary row shows the other source. Amber marks disagreements and review notes. N/A is rendered as “Не применяется” with an explanation of excluded weight. Claude's general notes stay separate from per-criterion evidence, which is absent in the original file.

The header wraps into two rows on phones. The remaining viewport belongs to the reader, with separate scroll areas for the list, criteria and transcript. New controls use existing colors, typography and focus rings.
