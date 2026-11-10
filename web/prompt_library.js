import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const API = "/prompt_library/api";
const MAX_ROWS = 200;

const uid = () => (globalThis.crypto?.randomUUID?.() || `pl-${Date.now()}-${Math.random().toString(36).slice(2)}`);
const clone = (value) => JSON.parse(JSON.stringify(value));
const emptyLibrary = (name) => ({ version: 1, name, groups: [], selections: [] });
const allPrompts = (library) => library.groups.flatMap((group) => group.prompts.map((prompt) => ({ ...prompt, group })));
const widget = (node, name) => node.widgets?.find((item) => item.name === name);

function h(tag, props = {}, ...children) {
    const element = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (key === "class") element.className = value;
        else if (key === "text") element.textContent = value;
        else if (key === "style") Object.assign(element.style, value);
        else if (key.startsWith("on") && typeof value === "function") element.addEventListener(key.slice(2).toLowerCase(), value);
        else if (value !== undefined && value !== null) element[key] = value;
    }
    for (const child of children.flat()) {
        if (child !== null && child !== undefined) element.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return element;
}
const button = (label, action, cls = "") => h("button", { type: "button", class: cls, text: label, onclick: action });
const field = (caption, control) => h("label", { class: "pl-field" }, h("span", { text: caption }), control);
const input = (value, placeholder = "") => h("input", { type: "text", value, placeholder });
const textarea = (value, placeholder = "") => h("textarea", { value, placeholder });

async function request(path, options = {}) {
    const response = await api.fetchApi(API + path, options);
    let body;
    try { body = await response.json(); } catch { throw new Error(`Server returned HTTP ${response.status}`); }
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
}

function parseSnapshot(value) {
    try {
        const data = JSON.parse(value || "{}");
        return data.version === 1 && Array.isArray(data.selected) ? data : { version: 1, selected: [] };
    } catch { return { version: 1, selected: [] }; }
}

function splitPrompts(text, mode) {
    const normalized = text.replace(/\r\n?/g, "\n");
    return (mode === "blocks" ? normalized.split(/\n\s*\n+/) : normalized.split("\n"))
        .map((item) => item.trim()).filter(Boolean);
}

function validateImportedLibrary(data) {
    if (!data || data.version !== 1 || !Array.isArray(data.groups) || !Array.isArray(data.selections ?? [])) {
        throw new Error("Not a version 1 Prompt Library JSON file.");
    }
    const ids = new Set();
    for (const group of data.groups) {
        if (!group || typeof group.name !== "string" || !Array.isArray(group.prompts)) throw new Error("Invalid group in JSON.");
        for (const prompt of group.prompts) {
            if (typeof prompt?.text !== "string" || !prompt.text.trim()) throw new Error("Invalid prompt text in JSON.");
            if (ids.has(prompt.id)) throw new Error("Duplicate prompt ID in JSON.");
            ids.add(prompt.id);
        }
    }
    return clone(data);
}

function promptIdentity(prompt) {
    // Two renders of one caption with different seeds are distinct replay items.
    if (prompt.source_file) return "source:" + prompt.source_file;
    const meta = prompt.seed != null || prompt.aspect_ratio || prompt.width || prompt.height;
    return "text:" + prompt.text.trim() + (meta
        ? "|" + String(prompt.seed ?? "") + "|" + String(prompt.aspect_ratio ?? "") +
          "|" + String(prompt.width ?? "") + "|" + String(prompt.height ?? "") : "");
}

function mergeLibrary(target, imported) {
    const used = new Set(allPrompts(target).map((prompt) => prompt.id));
    const remapped = new Map();
    for (const sourceGroup of imported.groups) {
        let group = target.groups.find((candidate) => candidate.name.toLowerCase() === sourceGroup.name.toLowerCase());
        if (!group) {
            group = { id: uid(), name: sourceGroup.name, keyword: sourceGroup.keyword || "", prompts: [] };
            target.groups.push(group);
        }
        const byIdentity = new Map(group.prompts.map((prompt) => [promptIdentity(prompt), prompt.id]));
        for (const prompt of sourceGroup.prompts) {
            const key = promptIdentity(prompt);
            let id = byIdentity.get(key);
            if (!id) {
                id = used.has(prompt.id) || !prompt.id ? uid() : prompt.id;
                group.prompts.push({ ...clone(prompt), id });
                used.add(id);
                byIdentity.set(key, id);
            }
            remapped.set(prompt.id, id);
        }
    }
    for (const entry of imported.selections || []) {
        if (!entry?.name || !Array.isArray(entry.prompt_ids)) continue;
        let name = entry.name;
        if (target.selections.some((existing) => existing.name === name)) name += " (imported)";
        const ids = [...new Set(entry.prompt_ids.map((id) => remapped.get(id)).filter(Boolean))];
        target.selections.push({ name, prompt_ids: ids });
    }
}

function downloadJSON(name, data) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + "\n"], { type: "application/json" }));
    const anchor = h("a", { href: url, download: `${name}.json` });
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function createManager(node) {
    const name = String(widget(node, "library_name")?.value || "default").trim();
    const snapshot = parseSnapshot(widget(node, "selection_json")?.value);
    const overlay = h("div", { class: "pl-overlay" });
    const modal = h("section", { class: "pl-modal", role: "dialog", ariaModal: "true", ariaLabel: "Prompt Library Manager", tabIndex: -1 });
    const body = h("div", { class: "pl-body" });
    const status = h("span", { class: "pl-status", text: "Loading library…" });
    const state = {
        name, lib: emptyLibrary(name), revision: null, selected: new Set(snapshot.selected.map((item) => item.id)),
        tab: "selection", groupId: null, search: "", show: MAX_ROWS, previewId: null, dirty: false,
        timer: null, queue: Promise.resolve(), saving: false, blocked: false, closed: false,
        imagePath: "", imageGroup: "Image Replay", imageKeyword: "replay",
        imageRecursive: false, imageScan: null, imageBusy: false,
    };
    let loadError = null;

    function message(text, bad = false) {
        status.textContent = text;
        status.classList.toggle("pl-error", bad);
    }
    function scheduleSave() {
        state.dirty = true;
        message("Unsaved library changes");
        clearTimeout(state.timer);
        state.timer = setTimeout(() => { void persist().catch((err) => message(err.message, true)); }, 700);
    }
    async function persist() {
        clearTimeout(state.timer);
        if (!state.dirty) return state.queue;
        if (state.blocked) throw new Error("Save blocked. Reload the library or export your work before closing.");
        const payload = clone(state.lib);
        payload.name = state.name;
        state.dirty = false;
        state.queue = state.queue.then(async () => {
            try {
                message("Saving…");
                const result = await request("/library", {
                    method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ name: state.name, library: payload, revision: state.revision }),
                });
                state.revision = result.revision;
                message(state.dirty ? "Unsaved library changes" : "Library saved");
            } catch (err) {
                state.dirty = true;
                state.blocked = true;
                message(err.message, true);
                throw err;
            }
        });
        return state.queue;
    }
    async function close(apply = false) {
        try {
            if (state.dirty || state.timer) await persist();
            else await state.queue;
        } catch (err) { message(err.message, true); return; }
        if (apply) {
            const selected = allPrompts(state.lib).filter((prompt) => state.selected.has(prompt.id));
            // Selected prompt text is stored in the workflow for portable execution.
            const selectionWidget = widget(node, "selection_json");
            if (!selectionWidget) {
                message("Selection widget is missing; cannot apply prompts.", true);
                return;
            }
            selectionWidget.value = JSON.stringify({
                version: 1, library: state.name,
                selected: selected.map((prompt) => {
                    const entry = { id: prompt.id, text: prompt.text };
                    for (const field of ["seed", "aspect_ratio", "width", "height", "source_file"]) {
                        if (Object.hasOwn(prompt, field)) entry[field] = prompt[field];
                    }
                    return entry;
                }),
            });
            // Keep the hidden snapshot in queued prompts and saved workflows.
            selectionWidget.options = { ...selectionWidget.options, serialize: true };
            selectionWidget.serialize = true;
            node._promptLibraryLastCount = undefined;
            widget(node, "library_name").value = state.name;
            updateNodeLabel(node);
            node.setDirtyCanvas?.(true, true);
            app.graph?.setDirtyCanvas?.(true, true);
        }
        state.closed = true;
        clearTimeout(state.timer);
        document.removeEventListener("keydown", onKeyDown, true);
        overlay.remove();
    }
    function onKeyDown(event) {
        if (event.key === "Escape") { event.stopPropagation(); void close(false); }
    }
    document.addEventListener("keydown", onKeyDown, true);
    modal.append(
        h("header", { class: "pl-header" },
            h("div", {}, h("h2", { text: "📚 Prompt Library" }), status),
            button("×", () => void close(false), "pl-close")),
        h("nav", { class: "pl-tabs" },
            ...[["selection", "Select prompts"], ["library", "Manage library"], ["sets", "Saved selections"]].map(([key, text]) =>
                button(text, () => { state.tab = key; render(); }, state.tab === key ? "pl-active" : ""))),
        body,
        h("footer", { class: "pl-footer" },
            h("span", { class: "pl-count", id: "pl-summary" }),
            h("div", { class: "pl-actions" },
                button("Export JSON", () => downloadJSON(state.name, state.lib)),
                button("Save library", () => void persist().catch((err) => message(err.message, true))),
                button("Close", () => void close(false)),
                button("Apply & close", () => void close(true), "pl-primary"))),
    );
    overlay.append(modal);
    document.body.append(overlay);
    modal.focus();

    function visiblePrompts() {
        const filter = state.search.trim().toLowerCase();
        return allPrompts(state.lib).filter((prompt) =>
            (!state.groupId || prompt.group.id === state.groupId) &&
            (!filter || `${prompt.text} ${prompt.group.name} ${prompt.group.keyword || ""}`.toLowerCase().includes(filter)));
    }
    function summary() {
        const total = allPrompts(state.lib).length;
        const count = allPrompts(state.lib).filter((prompt) => state.selected.has(prompt.id)).length;
        modal.querySelector("#pl-summary").textContent = `${count} selected / ${total} prompts · ${state.lib.groups.length} groups`;
    }
    function changeSelection(ids, action) {
        for (const id of ids) {
            if (action === "on") state.selected.add(id);
            else if (action === "off") state.selected.delete(id);
            else if (state.selected.has(id)) state.selected.delete(id);
            else state.selected.add(id);
        }
        render();
    }
    function groupList() {
        const root = h("aside", { class: "pl-groups" });
        root.append(h("h3", { text: "Groups" }), button(`All groups (${allPrompts(state.lib).length})`, () => { state.groupId = null; render(); }, state.groupId === null ? "pl-active" : ""));
        for (const group of state.lib.groups) {
            const ids = group.prompts.map((prompt) => prompt.id);
            const selected = ids.filter((id) => state.selected.has(id)).length;
            const box = h("input", { type: "checkbox", checked: ids.length > 0 && selected === ids.length, onchange: () => changeSelection(ids, selected === ids.length ? "off" : "on") });
            box.indeterminate = selected > 0 && selected < ids.length;
            root.append(h("div", { class: `pl-group ${state.groupId === group.id ? "pl-active" : ""}` },
                box,
                button(`${group.name}  (${selected}/${ids.length})`, () => { state.groupId = group.id; render(); }, "pl-group-link")));
        }
        return root;
    }
    function previewEditor(prompt) {
        const editor = textarea(prompt.text);
        editor.rows = 6;
        return h("div", { class: "pl-preview" }, editor,
            h("div", { class: "pl-actions" },
                button("Save edit", () => {
                    const value = editor.value.trim();
                    if (!value) { message("Prompt text cannot be empty", true); return; }
                    prompt.group.prompts.find((item) => item.id === prompt.id).text = value;
                    scheduleSave(); render();
                }, "pl-primary"),
                button("Delete prompt", () => {
                    if (!confirm("Delete this prompt? Saved selections referencing it will no longer include it.")) return;
                    prompt.group.prompts = prompt.group.prompts.filter((item) => item.id !== prompt.id);
                    state.selected.delete(prompt.id);
                    scheduleSave(); render();
                })));
    }
    function selectionTab() {
        const view = h("div", { class: "pl-select-view" });
        const search = input(state.search, "Search prompt text, group or keyword…");
        search.className = "pl-search";
        search.addEventListener("input", () => { state.search = search.value; state.show = MAX_ROWS; render(); document.querySelector(".pl-search")?.focus(); });
        const rows = h("div", { class: "pl-rows" });
        const matches = visiblePrompts();
        for (const prompt of matches.slice(0, state.show)) {
            const checked = state.selected.has(prompt.id);
            const check = h("input", { type: "checkbox", checked, onchange: () => {
                if (checked) state.selected.delete(prompt.id); else state.selected.add(prompt.id);
                const scroll = rows.scrollTop;
                render();
                document.querySelector(".pl-rows").scrollTop = scroll;
            } });
            rows.append(h("div", { class: "pl-row" },
                h("div", { class: "pl-row-summary" }, check,
                    button(prompt.text.replace(/\s+/g, " ").slice(0, 170), () => { state.previewId = state.previewId === prompt.id ? null : prompt.id; render(); }, "pl-prompt-preview"),
                    h("span", { class: "pl-chip", text: prompt.group.name }),
                    prompt.aspect_ratio ? h("span", { class: "pl-chip", text: prompt.aspect_ratio }) : null,
                    prompt.seed != null ? h("span", { class: "pl-chip", text: "seed " + prompt.seed }) : null,
                    prompt.source_file ? h("span", {
                        class: "pl-chip", title: prompt.source_file,
                        text: prompt.source_file.split(/[\\/]/).pop(),
                    }) : null),
                state.previewId === prompt.id ? previewEditor(prompt) : null));
        }
        if (matches.length > state.show) rows.append(button(`Show next ${Math.min(MAX_ROWS, matches.length - state.show)} of ${matches.length}`, () => { state.show += MAX_ROWS; render(); }));
        const countInput = h("input", { type: "number", min: 1, max: matches.length || 1, value: Math.min(10, matches.length), class: "pl-small-number" });
        view.append(groupList(), h("main", { class: "pl-prompts" },
            h("div", { class: "pl-toolbar" }, search,
                button("All shown", () => changeSelection(matches.map((prompt) => prompt.id), "on")),
                button("None shown", () => changeSelection(matches.map((prompt) => prompt.id), "off")),
                button("Invert", () => changeSelection(matches.map((prompt) => prompt.id), "invert"))),
            h("div", { class: "pl-subtoolbar" }, h("span", { text: `${matches.length} matching prompts` }),
                button("Random select N", () => {
                    const shuffled = [...matches].sort(() => Math.random() - 0.5);
                    const size = Math.max(0, Math.min(matches.length, Number(countInput.value) || 0));
                    changeSelection(shuffled.slice(0, size).map((prompt) => prompt.id), "on");
                }), countInput),
            rows));
        return view;
    }

    function imageImportPanel() {
        const pane = h("div", { class: "pl-card" });
        const directory = input(state.imagePath, "/workspace/ComfyUI/output/selected");
        const group = input(state.imageGroup, "Image Replay");
        const keyword = input(state.imageKeyword, "replay");
        const recursive = h("input", { type: "checkbox", checked: state.imageRecursive });
        const recordOptions = () => {
            state.imagePath = directory.value.trim();
            state.imageGroup = group.value.trim();
            state.imageKeyword = keyword.value.trim();
            state.imageRecursive = recursive.checked;
        };
        pane.append(
            h("h3", { text: "Import image prompts for replay" }),
            h("p", { text: "Scan a directory on the ComfyUI SERVER (e.g. RunPod), not your browser. Extract embedded prompt, seed, and image aspect ratio; review before importing." }),
            field("Server directory", directory),
            field("Group name", group),
            field("Optional group keyword", keyword),
            field("Include subdirectories", recursive),
            button(state.imageBusy ? "Scanning…" : "Scan image metadata", async () => {
                if (state.imageBusy) return;
                recordOptions();
                if (!state.imagePath) { message("Enter an image directory path", true); return; }
                state.imageBusy = true;
                state.imageScan = null;
                message("Scanning image files on the server…");
                try {
                    const scan = await request("/image-import", {
                        method: "POST", headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ directory: state.imagePath, recursive: state.imageRecursive }),
                    });
                    state.imageScan = scan;
                    message(scan.entries.length + " replay prompts found; " + scan.skipped_count + " skipped.");
                } catch (err) {
                    message(err.message, true);
                } finally {
                    state.imageBusy = false;
                    render();
                }
            }, "pl-primary"),
        );
        const scan = state.imageScan;
        if (scan) {
            const missingSeeds = scan.entries.filter((entry) => entry.seed == null).length;
            pane.append(
                h("p", { text: scan.entries.length + " of " + scan.scanned + " images contain recoverable prompts. " +
                    scan.skipped_count + " skipped. " + missingSeeds + " prompts without recoverable seeds." }),
                h("div", { class: "pl-image-report" },
                    ...scan.entries.slice(0, 8).map((entry) =>
                        h("p", { text: (entry.source_file.split(/[\\/]/).pop()) +
                            " | seed " + (entry.seed ?? "missing") +
                            " | " + entry.aspect_ratio + " | " + entry.text.replace(/\s+/g, " ").slice(0, 110) })),
                    ...(scan.skipped.length
                        ? [h("p", { text: "Skipped examples: " + scan.skipped.slice(0, 4).map((item) =>
                            item.filename + " (" + item.reason + ")").join("; ") })] : []),
                ),
                button("Import " + scan.entries.length + " prompts into group", () => {
                    recordOptions();
                    if (!state.imageGroup) { message("Group name is required", true); return; }
                    if (!scan.entries.length) { message("No prompts available to import", true); return; }
                    let target = state.lib.groups.find((item) =>
                        item.name.toLowerCase() === state.imageGroup.toLowerCase());
                    if (!target) {
                        target = { id: uid(), name: state.imageGroup, keyword: state.imageKeyword, prompts: [] };
                        state.lib.groups.push(target);
                    } else if (state.imageKeyword) {
                        target.keyword = state.imageKeyword;
                    }
                    const seen = new Set(target.prompts.map(promptIdentity));
                    let added = 0;
                    for (const entry of scan.entries) {
                        const key = promptIdentity(entry);
                        if (seen.has(key)) continue;
                        const item = { id: uid(), ...entry };
                        target.prompts.push(item);
                        state.selected.add(item.id);
                        seen.add(key);
                        added++;
                    }
                    state.imageScan = null;
                    state.groupId = target.id;
                    state.tab = "selection";
                    scheduleSave();
                    render();
                    message("Imported and selected " + added + " new replay prompts.");
                }, "pl-primary"),
            );
        }
        return pane;
    }

    function libraryTab() {
        const pane = h("div", { class: "pl-library-tab" });
        const filename = input(state.name);
        const available = h("datalist", { id: "pl-available-libraries" });
        filename.setAttribute("list", "pl-available-libraries");
        void request("/libraries").then((value) => {
            for (const item of value.libraries) available.append(h("option", { value: item }));
        }).catch(() => {});
        pane.append(h("div", { class: "pl-card" },
            h("h3", { text: "Library file" }),
            h("p", { text: "Use a simple name, without .json. Changing the name loads an existing library or starts an empty one." }),
            h("div", { class: "pl-inline" }, filename, available,
                button("Load / New", async () => {
                    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$/.test(filename.value) || filename.value === "." || filename.value === "..") {
                        message("Invalid library file name", true); return;
                    }
                    if (state.dirty && !confirm("Save current library before switching?")) return;
                    try {
                        await persist();
                        const result = await request(`/library?name=${encodeURIComponent(filename.value)}`);
                        state.name = filename.value;
                        state.lib = result.library;
                        state.revision = result.revision;
                        state.selected = new Set();
                        state.groupId = null;
                        state.blocked = false;
                        message("Library loaded"); render();
                    } catch (err) { message(err.message, true); }
                }, "pl-primary"))));

        const groupPicker = h("select");
        for (const group of state.lib.groups) groupPicker.append(h("option", { value: group.id, text: group.name }));
        const active = state.lib.groups.find((g) => g.id === state.groupId) || state.lib.groups[0];
        if (active) groupPicker.value = active.id;
        const groupName = input(active?.name || "");
        const groupKeyword = input(active?.keyword || "");
        groupPicker.addEventListener("change", () => { state.groupId = groupPicker.value; render(); });
        pane.append(h("div", { class: "pl-card" },
            h("h3", { text: "Edit group" }),
            field("Group", groupPicker), field("Group name", groupName), field("Optional keyword", groupKeyword),
            h("div", { class: "pl-actions" },
                button("Update group", () => {
                    if (!active) return;
                    if (!groupName.value.trim()) { message("Group name required", true); return; }
                    active.name = groupName.value.trim(); active.keyword = groupKeyword.value.trim();
                    scheduleSave(); render();
                }),
                button("Delete group", () => {
                    if (!active || !confirm(`Delete group “${active.name}” and its ${active.prompts.length} prompts?`)) return;
                    for (const prompt of active.prompts) state.selected.delete(prompt.id);
                    state.lib.groups = state.lib.groups.filter((group) => group.id !== active.id);
                    state.groupId = null;
                    scheduleSave(); render();
                }))));
        const importGroup = input("", "Group name (new or existing)");
        const importKeyword = input("", "Optional keyword, e.g. lighting");
        const importMode = h("select", {}, h("option", { value: "lines", text: "One prompt per line" }), h("option", { value: "blocks", text: "Blank-line separated blocks" }));
        const importText = textarea("", "Paste prompts here, without trigger text…");
        importText.rows = 10;
        const detected = h("span", { text: "0 prompts detected" });
        const updateDetected = () => { detected.textContent = `${splitPrompts(importText.value, importMode.value).length} prompts detected`; };
        importText.addEventListener("input", updateDetected);
        importMode.addEventListener("change", updateDetected);
        pane.append(h("div", { class: "pl-card" },
            h("h3", { text: "Import text prompts" }),
            h("p", { text: "Paste directly from your old text boxes. Periods are never treated as separators; duplicate prompts in the group are skipped." }),
            field("Group name", importGroup), field("Optional group keyword", importKeyword), field("Split mode", importMode),
            field("Paste prompts", importText),
            h("div", { class: "pl-inline" }, detected, button("Import prompts", () => {
                const name = importGroup.value.trim();
                if (!name) { message("Enter a group name first", true); return; }
                let group = state.lib.groups.find((item) => item.name.toLowerCase() === name.toLowerCase());
                if (!group) { group = { id: uid(), name, keyword: importKeyword.value.trim(), prompts: [] }; state.lib.groups.push(group); }
                else if (importKeyword.value.trim()) group.keyword = importKeyword.value.trim();
                const seen = new Set(group.prompts.map((prompt) => prompt.text.trim()));
                let added = 0;
                for (const text of splitPrompts(importText.value, importMode.value)) {
                    if (seen.has(text)) continue;
                    group.prompts.push({ id: uid(), text });
                    seen.add(text); added++;
                }
                state.groupId = group.id;
                scheduleSave(); render();
                message(`Imported ${added} new prompts into ${name}`);
            }, "pl-primary"))));
        const file = h("input", { type: "file", accept: ".json,application/json" });
        const importAction = h("select", {}, h("option", { value: "merge", text: "Merge groups and prompts" }), h("option", { value: "replace", text: "Replace current library" }));
        pane.append(h("div", { class: "pl-card" }, h("h3", { text: "Import / export JSON" }),
            h("div", { class: "pl-inline" }, file, importAction,
                button("Import JSON", async () => {
                    const selectedFile = file.files?.[0];
                    if (!selectedFile) { message("Choose a JSON file first", true); return; }
                    try {
                        const imported = validateImportedLibrary(JSON.parse(await selectedFile.text()));
                        if (importAction.value === "replace") {
                            if (!confirm("Replace all existing groups and saved selections in this library?")) return;
                            state.lib = imported;
                            state.selected.clear();
                        } else mergeLibrary(state.lib, imported);
                        state.lib.name = state.name;
                        state.groupId = null;
                        scheduleSave(); render();
                        message("JSON imported; library will auto-save");
                    } catch (err) { message(err.message, true); }
                }), button("Export JSON", () => downloadJSON(state.name, state.lib)))));
        pane.append(imageImportPanel());
        return pane;
    }
    function setsTab() {
        const pane = h("div", { class: "pl-sets-tab" });
        const setName = input("", "Name this selection");
        pane.append(h("div", { class: "pl-card" },
            h("h3", { text: "Save current selection" }),
            h("div", { class: "pl-inline" }, setName, button("Save selection", () => {
                const name = setName.value.trim();
                if (!name) { message("Enter a selection name", true); return; }
                const ids = allPrompts(state.lib).map((prompt) => prompt.id).filter((id) => state.selected.has(id));
                const existing = state.lib.selections.find((item) => item.name === name);
                if (existing) existing.prompt_ids = ids;
                else state.lib.selections.push({ name, prompt_ids: ids });
                scheduleSave(); render();
            }, "pl-primary"))));
        for (const set of state.lib.selections) {
            pane.append(h("div", { class: "pl-set-row" },
                h("span", { text: `${set.name} (${set.prompt_ids.length} prompts)` }),
                button("Load selection", () => { state.selected = new Set(set.prompt_ids); state.tab = "selection"; render(); }),
                button("Delete", () => {
                    if (!confirm(`Delete saved selection “${set.name}”?`)) return;
                    state.lib.selections = state.lib.selections.filter((entry) => entry !== set);
                    scheduleSave(); render();
                })));
        }
        return pane;
    }
    function render() {
        if (state.closed) return;
        const oldScroll = body.scrollTop;
        body.replaceChildren(state.tab === "library" ? libraryTab() : state.tab === "sets" ? setsTab() : selectionTab());
        body.scrollTop = oldScroll;
        [...modal.querySelectorAll(".pl-tabs button")].forEach((tab, i) => tab.classList.toggle("pl-active", ["selection", "library", "sets"][i] === state.tab));
        summary();
    }
    (async () => {
        try {
            const result = await request(`/library?name=${encodeURIComponent(state.name)}`);
            state.lib = result.library;
            state.revision = result.revision;
            message(result.revision ? "Library loaded" : "New library — import some prompts");
            render();
        } catch (err) {
            loadError = err;
            message(`Cannot load library: ${err.message}`, true);
            body.replaceChildren(h("p", { text: "The saved workflow snapshot remains executable. Fix the library server error before editing." }));
        }
    })();
    return overlay;
}

function updateNodeLabel(node) {
    const snapshot = parseSnapshot(widget(node, "selection_json")?.value);
    const control = node.widgets?.find((item) => item.name === "📚 Open Prompt Manager" || item._promptLibraryButton);
    if (control) {
        control._promptLibraryButton = true;
        control.name = "📚 Open Prompt Manager";
        const lastRun = Number.isInteger(node._promptLibraryLastCount)
            ? " · ran " + node._promptLibraryLastCount
            : " · not run";
        control.label = "📚 Manage prompts (" + snapshot.selected.length + " selected" + lastRun + ")";
    }
}

// Ensure a single stylesheet, even after extensions or workflows are reloaded.
const styleId = "comfy-prompt-library-styles";
if (!document.getElementById(styleId)) {
    const sheet = h("link", { id: styleId, rel: "stylesheet", href: new URL("./prompt_library.css", import.meta.url).href });
    document.head.append(sheet);
}

app.registerExtension({
    name: "markwelshboy.PromptLibrary",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "PromptLibraryNode") return;
        const previous = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = previous?.apply(this, args);
            const stored = widget(this, "selection_json");
            if (stored) {
                // Keep the embedded snapshot serialized but off the node canvas.
                stored.options = { ...stored.options, serialize: true };
                stored.serialize = true;
                stored.computeSize = () => [0, -4];
                stored.hidden = true;
            }
            const buttonWidget = this.addWidget("button", "📚 Open Prompt Manager", null, () => createManager(this));
            buttonWidget._promptLibraryButton = true;
            updateNodeLabel(this);
            this.setSize([Math.max(300, this.size[0]), this.computeSize()[1]]);
            return result;
        };

        const previousExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            previousExecuted?.apply(this, arguments);
            const receipt = message?.prompt_library?.[0];
            if (receipt && Number.isInteger(receipt.count)) {
                this._promptLibraryLastCount = receipt.count;
                this._promptLibraryLastPreview = receipt.preview || [];
            }
            updateNodeLabel(this);
            app.graph?.setDirtyCanvas?.(true, true);
        };
    },
});
