# Review interface comparison (Homework 4, Part A)

The interface is in `analysis/review_app/`: `load.py` builds the reviewable
records, `server.py` serves them and stores judgments, `ui/index.html` is the
app, `sampling.py` builds batches and the map projection, `depth_search.py`
retrieves candidates for a confirmed mode, and `sync_scores.py` repairs the
Langfuse mirror. It runs with `uv run python analysis/review_app/server.py` on
port 8030.

## Reviewing in the standard Langfuse view first

Before any code, the reviewer annotated traces in the ordinary Langfuse view
and recorded four sources of friction:

1. Each step had to be opened individually; the sequence was never visible at once.
2. The agent's narration could not be told apart from the reply the customer sees.
3. Nothing labelled the *kind* of case, so a trace could not be placed without reading it.
4. There was no way to move between cases of the same kind.

Every design decision below answers one of those.

## One design retained from the reference interface

**The file-backed HTTP API and the state layout.** The reference server
(`analysis/server.py`) exposes `/api/samples`, `/api/annotations`,
`/api/graph`, `/api/patterns` and `/api/suggestions` over plain JSON files in
`analysis/state/`, with the app posting on every change. That contract was kept
exactly, so the same state files, the same agent watch loop and the reference
app all still work against this server. The inline annotation flow was kept
too: select text, a popover appears, Enter saves, and the note appears in a
right margin aligned with its highlight, with hover linking both ways.

## One design changed after inspecting the traces

**Conversations, not traces, and narration rendered as its own kind of step.**

Cartwheel writes one Langfuse trace per user turn, so the 305 traces from
Homework 3 are really 250 conversations. `load.py` groups them by
`cartwheel.session_id` and renders the whole conversation in order.

The larger change is what a turn contains. The reference normalizer builds a
turn from the root span's input and output plus the tool observations, which
drops the agent's narration entirely, because narration is not on the root
span: it lives inside the *next* model call's input array, since that call is
shown the conversation so far. Yet narration is where the agent states what it
is about to do and why, and three of the seven failure modes are about exactly
that. So `load.py` reconstructs each turn from its last model call, and the app
renders three visually distinct things: narration (dashed border, italic,
muted, "agent, before a tool call"), tool call and result together in one
bordered container, and the final reply in a full-contrast block labelled
"agent reply to the user". Encrypted thinking blocks render as a one-line
marker rather than being passed off as reasoning.

Two smaller adaptations came from the same session: a metadata chip row per
conversation (role, intent, difficulty, group, policy, record state, user
style, damaged-record case) where every chip is a filter, plus a "next in
group" control; and header flags computed at load time for tool errors,
permission denials, a missing reply, and an unusually long tool sequence.

Because this dataset has an answer key from Homework 3, each conversation also
carries its expected result, collapsed by default so open coding is not
anchored by it, with `e` to reveal.

## One limitation that remains

**Replies render as raw Markdown.** A reply containing `**can**` shows the
asterisks. Rendering it would break the character offsets that anchor
annotations to their highlighted span, so the text is shown verbatim. Fixing it
properly means anchoring annotations to a rendered DOM range instead of a
string offset.

Two smaller ones: only the first highlight in a block is rendered, so two
annotations on the same block show one highlight and two margin notes; and
runs are loaded from the committed Homework 3 export by default, with
`--source langfuse` needed to pull live traces.

## Defect found and fixed during use

Rejecting an agent suggestion originally deleted it. The handout requires the
saved state to contain at least one rejected suggestion, so both decisions now
keep the record with `status` and a timestamp, and accepting prompts the
reviewer for a note in their own words before saving it as their annotation.
