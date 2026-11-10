# ComfyUI-PromptLibrary

A visual, portable prompt library for ComfyUI. Replace stacks of giant text widgets with one compact node and a **large, resizable prompt manager**.

## Features

- Paste prompts from existing multiline nodes. Split **per line** or at **blank-line-separated blocks** (for prompts that themselves contain line breaks). Periods are never separators.
- Organize prompts into named groups with an optional searchable keyword.
- Browse, search and edit prompts in a modal. Select individual prompts or whole groups (with partial-selection states), select/invert a filtered subset, or add a random selection.
- Save named selections; export or import a complete JSON library (merge or replace).
- Keep libraries as standard JSON files **outside the custom node repository**.
- **Workflow-portable**: applying a selection embeds its text into the node's hidden `selection_json` widget. The workflow can still run without the library file on another machine.
- Output a real ComfyUI `OUTPUT_IS_LIST` of individual `STRING`s, plus newline-separated text and count.
- Import image metadata for prompt/seed/ratio replay; emit index-aligned seed, aspect ratio, width and height lists.
- Optional trigger prefix; Selected, Random N (seeded), or Sequential N (starting at zero-based index) output modes.
- No extra Python or frontend packages beyond the standard ComfyUI installation.

## Install

```bash
cd /workspace/ComfyUI/custom_nodes  # or your ComfyUI/custom_nodes directory
git clone https://github.com/markwelshboy/ComfyUI-PromptLibrary.git
```

Restart ComfyUI and **hard-refresh your browser**. Add **📚 Prompt Library** from the `Prompt Library` category.

## Quick start

1. Click **Open Prompt Manager**, open **Manage library**, paste old prompt lists into the import box, give each group a name (and optionally a keyword), and click **Import prompts**.
2. In **Select prompts**, check groups or individual prompts. Search across groups, and click prompt text to preview/edit its complete content.
3. Optionally save the current selection in **Saved selections**. Click **Apply & close** to embed the chosen prompts in your workflow.
4. On the node, set `trigger`, `mode`, `max_prompts` (`0` means all), `start_index`, and `seed`; connect the **prompts** output to the downstream `STRING` input.

**Important:** `prompts` is a native ComfyUI list-execution output (one call per selected prompt for ordinary downstream nodes), whereas `prompts_text` is one single newline-separated string. The text output may be ambiguous if a prompt itself contains newlines; use `prompts` for individual prompt processing. If no prompts are selected, `prompts` is empty and downstream list-executed nodes might not run.



## Image replay: import a directory of selected images

The **📚 Prompt Library** node can import replay entries from selected images stored
on the ComfyUI **server**. Open **Manage prompts → Manage library → Import image
prompts for replay**. Provide the full directory path *on the RunPod/server*,
a group name (e.g., "Krea2 favorites"), an optional keyword, and whether to
scan subdirectories.

1. Choose **Scan image metadata**. The modal reports scanned, recoverable,
   and skipped files, plus a preview of filenames, seeds and aspect ratios.
2. Click **Import N prompts into group**. New records are selected automatically.
   Importing the same source file again skips duplicates, but two different
   source images with the same caption remain distinct (they may have
   different seeds or dimensions).
3. Select/deselect entries and **Apply & close**. Save the ComfyUI workflow.
4. Run the usual **Selected**, **Random N**, or **Sequential N** list mode;
   all outputs remain index-aligned after slicing/shuffling.

Replay inputs are **PNG, JPEG or WebP** with supported metadata. Supported
sources include A1111-style \`parameters\` and ComfyUI execution \`prompt\`
graphs with traceable positive text and sampler seeds (including basic
KSampler and SamplerCustomAdvanced/RandomNoise paths). Files without
recoverable positive prompt text are **skipped**, not guessed. Heavily
customized pipelines, missing metadata, or images whose metadata only
contains a static graph rather than a resolved dynamic prompt may not
be reconstructable. The scan does not import pixel data into the JSON.

### 📚 Prompt Library output sockets

The first three sockets remain in their original positions. New sockets
are **appended** so saved workflows keep existing connections:

| Output | Data type | Purpose |
|---|---|---|
| \`prompts\` | STRING list | One caption per downstream execution |
| \`prompts_text\` | STRING | Newline-delimited diagnostic |
| \`prompt_count\` | INT | Count of executed entries |
| \`seeds\` | INT list | Original image's sampler seed |
| \`aspect_ratios\` | STRING list | Ratio such as \`4:5\` |
| \`widths\` | INT list | Exported image pixel width |
| \`heights\` | INT list | Exported image pixel height |

**Unknown values:** Missing seeds are \`-1\` (intentional invalid sentinel,
not a fabricated seed); missing aspect ratios are \`""\`; missing
width/height are \`0\`. Normal manually typed prompts therefore have
unknown seed/dimensions until imported with metadata. Do not connect
\`seeds\` for those entries to a sampler unless you supply a fallback.

Seed values are stored in JSON as **decimal strings** to preserve all
64 bits across the browser's JavaScript serialization; the ComfyUI socket
converts them back to exact INTs during execution. Image-file paths are
provenance labels; selected text and metadata are embedded into the
workflow, so replay does not need the original image directory at
execution time.

For an exact replay, leave the node's \`trigger\` input empty if the
recovered prompt already includes its LoRA trigger. Images may have been
upscaled/cropped; \`widths\`/\`heights\` reflect **saved image dimensions**,
not necessarily the original sampling latent. Ratios are simplified to
common proportions where the dimensions are very close.

**Directory access:** A browser file chooser cannot read a RunPod
directory; this importer uses a server-side absolute path. Optionally
set \`COMFYUI_PROMPT_LIBRARY_IMAGE_ROOT=/workspace/ComfyUI/output\` before
starting ComfyUI to restrict which folders the API can scan. Use
ComfyUI's normal access control; anyone able to call the API with
access to your instance may inspect readable image prompt metadata.

The replay outputs use native ComfyUI **list execution** within a single
queued graph. They do not automatically schedule separate queue jobs
or persist progress. For a large Krea2 render set, verify the downstream
samplers handle lists without exhausting VRAM; automatic one-per-run
queueing is currently implemented for the separate Dataset Prompt Queue.


## Checking that prompts actually execute

The button displays **3 selected · not run** until ComfyUI executes the node.
After it executes, the label changes to **3 selected · ran 3** (or **ran 0**
if the backend received an empty snapshot). You can queue the Prompt Library
**by itself**; it is an output node for diagnostic purposes.

For an easy sanity check, connect the **prompts_text** output (the middle
STRING output) to a **Show Text** node. That is one newline-separated string.
The top **prompts** output uses `OUTPUT_IS_LIST` to run downstream nodes
once per prompt. With `prompts`, a list-aware consumer is recommended.

If the button says **ran 3** but a third-party Show Text remains blank,
the Prompt Library executed successfully; the display extension may have a
frontend compatibility problem. Some versions of Custom-Scripts Show Text
have broken after ComfyUI frontend updates. Inspect browser devtools for
errors in `showText.js`, update the custom script, or try another text
preview node. If the button still says **not run**, check the ComfyUI
terminal and the frontend's queue/validation errors, and verify the node
is not muted or bypassed.

## Persistence and portability

Libraries are saved as `user/default/prompt_library/<name>.json` relative to ComfyUI's user directory (normally `ComfyUI/user/default/prompt_library`), or in `COMFYUI_PROMPT_LIBRARY_DIR` when that environment variable is set. **On ephemeral pods, mount this directory on persistent storage**, or export the JSON and import it on the next pod.

Each ComfyUI installation owns its own library files. The **selected prompt snapshot** in a saved workflow is independent of those files; it won't automatically update when you edit a library elsewhere. Apply the selection again to refresh it. Saved selections are library-wide; current checkbox selections are specific to each node/workflow.

The JSON schema is version 1:

```json
{
  "version": 1,
  "name": "My Prompts",
  "groups": [
    {
      "id": "lighting",
      "name": "Lighting & Camera",
      "keyword": "light",
      "prompts": [
        { "id": "light-01", "text": "Standing by a window, soft side lighting" }
      ]
    }
  ],
  "selections": [
    { "name": "Portrait tests", "prompt_ids": ["light-01"] }
  ]
}
```

Import merge matches groups by name, skips identical prompt text within each group and remaps saved-selection prompt IDs as needed. JSON exports contain groups, prompts and saved selections; the optional trigger is intentionally *not* stored in prompt text.

The backend enforces a 5 MB library size limit, unique prompt/group IDs and safe file names; saves are atomic, and concurrent editing uses revision checks to prevent silent overwrites. A server with multiple simultaneous editors should still avoid editing the same library in parallel.

## Testing

Run standard-library tests without ComfyUI:

```bash
python -m unittest discover -s tests -v
node --check web/prompt_library.js  # if Node.js is installed
```

## Known limitations

- UI extension behavior has not yet been manually exercised in every ComfyUI frontend release. Reload after install, and report any frontend-console errors.
- For now, group selection and text editing operate inside the modal, not as live socket inputs from existing prompt-list nodes: paste/import the old content once.
- Large selected snapshots increase workflow JSON size. The library can be much larger than the currently applied selection.
- The default library directory is server-wide, designed for self-hosted ComfyUI; it is not an isolated, permissioned multi-user library service.


## Dataset Prompt Browser (CSV / Parquet)

Add **📚 Dataset Prompt Browser** alongside the existing **📚 Prompt Library** node. The original manual prompt manager and its version 1 JSON format remain unchanged.

This node adds a database-backed, **non-executing** dataset explorer. Browse/filter prompts in a large modal without queuing ComfyUI or passing data through the inference graph. **Apply Selection** is the explicit point at which selected captions are embedded into the node's hidden workflow snapshot.

### Quick start with the stablellama dataset

1. Pull this custom node, restart ComfyUI and hard-refresh the browser.
2. Add **Prompt Library → 📚 Dataset Prompt Browser**.
3. Click **Browse dataset** → **Import HF CSV**. The source is [stablellama/erotic-image-prompts](https://huggingface.co/datasets/stablellama/erotic-image-prompts), file captions.csv. The dataset documents **82,880** prompts. First import downloads and indexes the CSV; later sessions use cached SQLite.
4. Enable/disable metadata filters. Operators include numeric comparisons, exact/category matching, and clothing-tag contains/excludes. Searching updates result counts and pages without executing any graph nodes.
5. Enter **include/exclude caption tags** (press Enter; remove pills with ×). Include supports **ALL / ANY**; exclusions reject captions containing **any** excluded term.
6. Check individual results or **Check page**; alternatively set **Apply mode** to *All matching prompts* or *Random N from matches* (with a seed). **Apply Selection** freezes captions in the workflow; **Cancel** discards pending selection changes.
7. On the node configure **replace_name** (default: Alice) and **trigger_word** (for example your LoRA trigger). Literal, case-insensitive whole-word replacement happens **during execution**, without modifying the dataset or snapshot.
8. Connect **prompts** (ComfyUI list output) to the Krea2 conditioning flow. Other outputs are **prompts_text** (diagnostic joined string), **prompt_count**, **metadata_json** (source identifiers and per-row metadata), and **aspect_ratios** (STRING list, one CSV `Aspect Ratio` field per caption).

Dataset character names are placeholders: Alice/Bella/Clara/Diana/Emma and Adam/Bob/Charlie/David/Eric. The replacement field can target any one. An empty trigger leaves captions unchanged.

**Selection limit:** 2,000 prompts / 4 MiB, preventing huge workflow snapshots. If more match your filters, use **Random N** or narrow the filters. Samples with a fixed seed are reproducible. Checked prompts are retained while filtering and are cleared only when explicitly unchecked or cleared.

**Import options:**

- **Import HF CSV (recommended):** uses installed huggingface_hub plus Python's built-in csv and sqlite3. No pandas or datasets library.
- **Import HF Parquet:** uses native list tags and requires pyarrow in the ComfyUI Python environment (for example, /opt/venv/bin/python -m pip install pyarrow).
- **Upload CSV/Parquet:** local browser file up to 256 MiB indexed by ComfyUI. Expected columns include Caption, Females, Males, Artistic Quality, Erotic Score, Pornographic Score, Aspect Ratio, Nudity, Clothing. Missing metadata fields are accepted; a nonempty Caption is required. Clothing in CSV is a Python literal list, such as ['hat', 'dress'].

Filter presets are persisted in **browser localStorage per dataset**, not synchronized across devices. They never affect a node's selection until Apply. Selected caption **text** is stored in the saved workflow for offline execution, independently of source files.

### Storage and stateless pods

Indexed databases and source files are stored in:

    COMFYUI_PROMPT_LIBRARY_DIR/
    └── datasets/
        └── stablellama_erotic-image-prompts/
            ├── manifest.json
            ├── prompts.sqlite
            └── source/
                └── captions.csv

Without the environment variable, the location is ComfyUI/user/default/prompt_library/datasets/. On Vast/RunPod, point **COMFYUI_PROMPT_LIBRARY_DIR** at a persistent workspace directory (for example /workspace/prompt-library) **before starting ComfyUI**. Source downloads and the SQLite index stay out of Git; reimporting replaces indexes atomically. The existing manual Prompt Library JSON files use the same root.

### Browser API

All endpoints are same-origin under /prompt_library/api/; no additional port is needed.

- **GET /datasets:** cached datasets.
- **POST /datasets/import:** import the built-in Hugging Face dataset (format csv or parquet).
- **POST /datasets/upload:** multipart file upload.
- **GET /datasets/{id}/schema:** metadata definitions and categorical values.
- **POST /datasets/{id}/search:** validated filters/tags, paginated results, matched count.
- **POST /datasets/{id}/select:** freeze checked IDs, all matches, or seeded random sample.

Field/operator allowlists and bound SQL parameters protect the database. Regex/SQL authoring is deliberately excluded in this first version.

### Scope / remaining work

The browser, live filtering, CSV/Parquet importer, search tags, filter presets, deterministic snapshots, trigger replacement and list output are implemented. A **persistent queue controller** (pause/resume/retry for thousands of images) and semantic deduplication/diversity sampling are **not implemented yet**. Verify your particular Krea2 downstream nodes handle ComfyUI list execution as expected. Applying a selection never auto-queues generation.

The stablellama dataset uses a **CC-BY-4.0** license. Its aesthetic and erotic-content scores are LLM-estimated and not reliable ground truth. Follow licensing and consent requirements for generated content.


## Sequential prompt queue (one Krea2 image per execution)

**📚 Dataset Prompt Queue** is a third node, designed for prompt-by-prompt Krea2 generation. It shares the **Browse dataset → Apply Selection** modal with 📚 Dataset Prompt Browser, but emits **one STRING caption per Queue Prompt**, rather than the entire list. Using it prevents one queue execution from fanning out into hundreds of downstream Krea2 runs.

Add **Prompt Library → 📚 Dataset Prompt Queue** and click **Browse dataset** to import/filter/select prompts. Click **Apply Selection** to freeze them in the workflow and reset the queue to the first prompt. Wire the node's **prompt** output directly to your text-encoding/Krea2 conditioning path. Set **replace_name** (default: Alice) and **trigger_word** on the queue node for LoRA substitutions.

Compact controls are inspired by [shootthesound's MIT-licensed Sequential Image Loader](https://github.com/shootthesound/ComfyUI-SequentialImageLoader). The code is independently implemented for prompts.

| Control | Behavior |
|---|---|
| **Next: X / N** | Shows the caption that the next Queue Prompt will process. |
| **Scrub slider** | Jump to any selected caption without running it. |
| **⏮ Reset** | Move back to the first caption. |
| **‹ Prev / Next ›** | Move the pointer without queueing a new generation. |
| **⏸ Hold last / Hold next** | Freeze repeated executions on the most recently executed caption or the upcoming caption; **Resume** returns to the previous next-pointer position. |
| **▶ Queue remaining** | Queue one full workflow execution at a time, sequentially, until the last selected caption finishes. |
| **■ Stop queueing** | Cancel further automatic submissions without interrupting an already-running generation. |
| **Current / Next** | Caption previews (including the current trigger-word substitution) for the last executed and upcoming prompts. |

**Key implementation detail:** the frontend hooks ComfyUI's queue submission *after* asynchronous graph serialization, then advances its hidden **sequence_index** for the following queue. The Python node receives the index already serialized into that execution, emits one caption, and reports the **actual executed index/total** in its UI receipt. After an execution-success event, the queue controller requests the next run. Errors or interruptions stop auto-queue. The last prompt stops automatic queueing instead of looping forever.

**Outputs:** prompt (single STRING), index (0-based INT), total (INT), metadata_json (STRING), **aspect_ratio (single STRING)**. The metadata includes dataset/revision/record ID and row metadata. Aspect ratios come directly from the imported CSV/Parquet `Aspect Ratio` column (e.g. `2:3` or `4:5`), **not** from parsing prompt text; they are embedded in the workflow's frozen selection. Wire the ratio to an AR-to-dimensions/MP node that accepts a STRING ratio. Nodes expecting a fixed COMBO may need a converter. The original 📚 Dataset Prompt Browser **prompts** output remains a native multi-prompt ComfyUI list; its behavior has not changed.

**Limitations:** queue-all state is tied to the active browser/ComfyUI session, not a persistent server-side job. The current position is stored in the workflow as a hidden serialized widget, but currently-running jobs and completion/error history are not persisted. Avoid running multiple auto-queue controllers or using ComfyUI's built-in Auto Queue simultaneously on the same graph. For production-scale thousands-of-image work, a future server-backed job ledger should handle retries and resume-from-completed rather than assuming the pointer means success. This first implementation is subject to frontend validation against your deployed ComfyUI build.

