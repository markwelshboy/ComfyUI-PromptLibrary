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
8. Connect **prompts** (ComfyUI list output) to the Krea2 conditioning flow. Other outputs are **prompts_text** (diagnostic joined string), **prompt_count**, and **metadata_json** (source identifiers and per-row metadata).

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
