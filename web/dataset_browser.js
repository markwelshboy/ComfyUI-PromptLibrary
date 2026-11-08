import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const API = "/prompt_library/api";
const DEFAULT = "stablellama_erotic-image-prompts";
const MAX_SELECTION = 2000;
const widget = (node, name) => node.widgets?.find((item) => item.name === name);

function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (key === "class") node.className = value;
        else if (key === "text") node.textContent = value;
        else if (key === "style") Object.assign(node.style, value);
        else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
        else if (value !== null && value !== undefined) node[key] = value;
    }
    for (const child of children.flat()) {
        if (child === null || child === undefined) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}
function btn(title, callback, cls = "") {
    return el("button", { text: title, type: "button", class: cls, onclick: callback });
}
function option(value, label) { return el("option", { value, text: label }); }
function setOptions(select, items, selected) {
    select.replaceChildren(...items.map(([value, title]) => option(value, title)));
    if (selected !== undefined) select.value = selected;
}
function snapshot(value) {
    try {
        const result = JSON.parse(value || "{}");
        return result.version === 1 && Array.isArray(result.selected) ? result : { version: 1, selected: [] };
    } catch { return { version: 1, selected: [] }; }
}
async function request(path, options = {}) {
    const response = await api.fetchApi(API + path, options);
    let result;
    try { result = await response.json(); } catch { throw new Error("Server error (HTTP " + response.status + ")"); }
    if (!response.ok) throw new Error(result.error || "HTTP " + response.status);
    return result;
}
function post(path, data) {
    return request(path, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data),
    });
}
function presetKey(id) { return "comfy-prompt-library-dataset-presets:" + id; }
function loadPresets(id) {
    try { return JSON.parse(localStorage.getItem(presetKey(id)) || "{}"); }
    catch { return {}; }
}
function savePresets(id, presets) { localStorage.setItem(presetKey(id), JSON.stringify(presets)); }
function short(text, length = 200) { return text.length > length ? text.slice(0, length) + "…" : text; }
function formRow(label, control) { return el("label", { class: "pld-field" }, el("span", { text: label }), control); }

function updateLabel(node) {
    const data = snapshot(widget(node, "selection_json")?.value);
    const control = node.widgets?.find((w) => w._pldButton);
    if (control) {
        const run = Number.isInteger(node._pldLastRun) ? " · ran " + node._pldLastRun : " · not run";
        control.label = "📚 Browse dataset (" + data.selected.length + " selected" + run + ")";
    }
}

function openBrowser(node) {
    const applied = snapshot(widget(node, "selection_json")?.value);
    const state = {
        datasetId: applied.dataset || DEFAULT, fields: [], manifest: null, datasets: [],
        filters: [], include: [], exclude: [], includeMode: "all",
        mode: "manual", sampleSize: 100, seed: 42,
        selected: new Set(), page: 0, perPage: 50, matched: 0, total: 0,
        rows: [], busy: false, queryToken: 0, timer: null, closed: false,
    };
    for (const item of applied.selected) {
        if (typeof item.id === "string" && item.id.startsWith(state.datasetId + ":")) {
            const value = Number(item.id.slice(state.datasetId.length + 1));
            if (Number.isSafeInteger(value)) state.selected.add(value);
        }
    }

    const overlay = el("div", { class: "pld-overlay" });
    const modal = el("section", { class: "pld-modal", role: "dialog", ariaModal: "true",
        ariaLabel: "Dataset Prompt Browser", tabIndex: -1 });
    const message = el("span", { class: "pld-message", text: "Loading datasets…" });
    const stats = el("span", { class: "pld-stats" });
    const sourceSelect = el("select");
    const filterBox = el("div", { class: "pld-filter-list" });
    const tagBox = el("div", { class: "pld-tag-area" });
    const rowsBox = el("div", { class: "pld-rows" });
    const pageText = el("span", { class: "pld-page-text" });
    const modeSelect = el("select");
    setOptions(modeSelect, [
        ["manual", "Checked prompts"], ["all_matches", "All matching prompts"],
        ["random", "Random N from matches"],
    ], state.mode);
    const nInput = el("input", { type: "number", min: 1, max: MAX_SELECTION, value: state.sampleSize });
    const seedInput = el("input", { type: "number", min: 0, max: 4294967295, value: state.seed });
    const presetSelect = el("select");
    const fileInput = el("input", { type: "file", accept: ".csv,.parquet", style: { display: "none" } });
    const left = el("aside", { class: "pld-sidebar" });
    const main = el("main", { class: "pld-main" });
    const footer = el("footer", { class: "pld-footer" });

    function status(text, bad = false) {
        message.textContent = text;
        message.classList.toggle("pld-error", bad);
    }
    function summary() {
        stats.textContent = state.manifest
            ? state.total.toLocaleString() + " total · " + state.matched.toLocaleString()
                + " matches · " + state.selected.size.toLocaleString() + " checked"
            : "Import a dataset to begin";
        nInput.disabled = state.mode !== "random";
        seedInput.disabled = state.mode !== "random";
        updateApplyButton();
    }
    function filtersPayload() {
        return {
            filters: state.filters.map((filter) => ({ ...filter })),
            include: [...state.include], exclude: [...state.exclude],
            include_mode: state.includeMode,
        };
    }
    function refreshPresets() {
        const names = Object.keys(loadPresets(state.datasetId)).sort();
        setOptions(presetSelect, [["", "Load filter preset…"], ...names.map((name) => [name, name])], "");
    }
    function close() {
        state.closed = true;
        clearTimeout(state.timer);
        state.queryToken += 1;
        document.removeEventListener("keydown", onKeyDown, true);
        overlay.remove();
    }
    function onKeyDown(event) {
        if (event.key === "Escape") {
            event.stopPropagation();
            close();
        }
    }
    document.addEventListener("keydown", onKeyDown, true);
    function queueSearch(immediate = false) {
        if (!state.manifest || state.closed) return;
        clearTimeout(state.timer);
        state.timer = setTimeout(() => void search(), immediate ? 0 : 200);
    }
    async function search() {
        if (state.closed || !state.manifest) return;
        const token = ++state.queryToken;
        const id = state.datasetId;
        status("Searching captions…");
        try {
            const response = await post("/datasets/" + encodeURIComponent(id) + "/search", {
                ...filtersPayload(), page: state.page, per_page: state.perPage,
            });
            if (state.closed || id !== state.datasetId || token !== state.queryToken) return;
            state.rows = response.rows;
            state.matched = response.matched;
            state.total = response.total;
            renderRows();
            summary();
            status("Ready");
        } catch (error) {
            if (!state.closed && token === state.queryToken) status(error.message, true);
        }
    }
    function refreshResults() {
        state.page = 0;
        queueSearch();
    }

    function toggleRow(id, checked) {
        if (checked && state.selected.size >= MAX_SELECTION && !state.selected.has(id)) {
            status("Maximum checked selection is " + MAX_SELECTION + " prompts.", true);
            renderRows();
            return;
        }
        if (checked) state.selected.add(id);
        else state.selected.delete(id);
        summary();
    }
    function renderRows() {
        rowsBox.replaceChildren();
        if (!state.rows.length) {
            rowsBox.append(el("p", { class: "pld-muted", text: "No prompts match these filters." }));
        }
        for (const row of state.rows) {
            const checkbox = el("input", { type: "checkbox", checked: state.selected.has(row.id),
                onchange: (event) => toggleRow(row.id, event.target.checked) });
            const title = el("summary", { text: short(row.caption, 260) });
            const full = el("pre", { text: row.caption });
            const info = el("div", { class: "pld-meta" },
                el("span", { text: "#" + row.id }),
                el("span", { text: "♀ " + (row.females ?? "—") }),
                el("span", { text: "♂ " + (row.males ?? "—") }),
                el("span", { text: "Quality " + (row.artistic_quality ?? "—") }),
                el("span", { text: "Ratio " + (row.aspect_ratio || "—") }),
                el("span", { text: "Nudity " + (row.nudity || "—") }),
                el("span", { text: "Clothing " + (row.clothing?.join(", ") || "—") }));
            rowsBox.append(el("div", { class: "pld-row" }, checkbox,
                el("details", {}, title, full, info)));
        }
        const pages = Math.ceil(state.matched / state.perPage);
        pageText.textContent = "Page " + (state.page + 1) + " / " + Math.max(pages, 1);
        previous.disabled = state.page === 0;
        next.disabled = state.page + 1 >= pages;
    }
    function operators(kind) {
        const numeric = [
            ["equals", "="], ["not_equals", "≠"], ["gt", ">"], ["gte", "≥"],
            ["lt", "<"], ["lte", "≤"], ["between", "between (min,max)"],
        ];
        const textual = [
            ["contains", "contains"], ["not_contains", "does not contain"],
            ["equals", "equals"], ["not_equals", "not equals"],
        ];
        if (kind === "number") return numeric;
        if (kind === "tags") return textual;
        if (kind === "text") return [...textual, ["starts_with", "starts with"]];
        return [...textual, ["starts_with", "starts with"]];
    }
    function renderFilters() {
        filterBox.replaceChildren();
        for (const field of state.fields) {
            const filter = state.filters.find((f) => f.field === field.key);
            if (!filter) continue;
            const enabled = el("input", { type: "checkbox", checked: filter.enabled, title: "Enable " + field.label,
                onchange: (event) => { filter.enabled = event.target.checked; refreshResults(); } });
            const op = el("select", { class: "pld-operator",
                onchange: (event) => { filter.operator = event.target.value; refreshResults(); } });
            setOptions(op, operators(field.type), filter.operator);
            let value;
            if (field.options?.length) {
                value = el("select", { class: "pld-value",
                    onchange: (event) => { filter.value = event.target.value; refreshResults(); } });
                setOptions(value, [["", "Any"], ...field.options.map((item) => [item, item])], filter.value);
            } else {
                value = el("input", { class: "pld-value", type: "text", value: filter.value,
                    placeholder: field.type === "number" ? "e.g. 0.7" : "Search value",
                    oninput: (event) => { filter.value = event.target.value; refreshResults(); } });
            }
            filterBox.append(el("div", { class: "pld-filter" }, enabled,
                el("span", { class: "pld-filter-name", text: field.label }), op, value));
        }
    }
    function tagEditor(label, key) {
        const chips = el("div", { class: "pld-chips" });
        const entry = el("input", { type: "text", placeholder: "Type tag + Enter", maxLength: 120 });
        entry.addEventListener("keydown", (event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            event.stopPropagation();
            const value = entry.value.trim();
            if (!value || state[key].length >= 20) return;
            if (!state[key].some((old) => old.toLowerCase() === value.toLowerCase())) state[key].push(value);
            entry.value = "";
            renderTags();
            refreshResults();
        });
        return el("div", { class: "pld-tag-editor" }, el("strong", { text: label }), chips, entry);
    }
    const includeEditor = tagEditor("Include captions", "include");
    const excludeEditor = tagEditor("Exclude captions", "exclude");
    function renderTags() {
        for (const [key, editor] of [["include", includeEditor], ["exclude", excludeEditor]]) {
            const chips = editor.querySelector(".pld-chips");
            chips.replaceChildren(...state[key].map((tag) =>
                el("span", { class: "pld-tag" }, tag,
                    btn("×", () => { state[key] = state[key].filter((t) => t !== tag); renderTags(); refreshResults(); },
                        "pld-tag-close"))));
        }
    }
    const allRadio = el("input", { type: "radio", name: "pld-match-" + Math.random(), value: "all",
        checked: true });
    const anyRadio = el("input", { type: "radio", name: allRadio.name, value: "any" });
    allRadio.addEventListener("change", () => { state.includeMode = "all"; refreshResults(); });
    anyRadio.addEventListener("change", () => { state.includeMode = "any"; refreshResults(); });
    tagBox.append(includeEditor, excludeEditor,
        el("div", { class: "pld-match" }, "Include matching: ",
            el("label", {}, allRadio, " All"), el("label", {}, anyRadio, " Any")));

    function savePreset() {
        const name = window.prompt("Save filters as:", "My filter");
        if (!name || !name.trim()) return;
        const presets = loadPresets(state.datasetId);
        presets[name.trim().slice(0, 80)] = filtersPayload();
        savePresets(state.datasetId, presets);
        refreshPresets();
        status("Saved filter preset: " + name);
    }
    function applyPreset() {
        const data = loadPresets(state.datasetId)[presetSelect.value];
        if (!data) return;
        state.filters = state.fields.map((field) => {
            const old = data.filters?.find((f) => f.field === field.key);
            const ops = operators(field.type).map(([key]) => key);
            return { field: field.key, enabled: !!old?.enabled,
                operator: ops.includes(old?.operator) ? old.operator : ops[0],
                value: typeof old?.value === "string" ? old.value : "" };
        });
        state.include = Array.isArray(data.include) ? data.include.slice(0, 20) : [];
        state.exclude = Array.isArray(data.exclude) ? data.exclude.slice(0, 20) : [];
        state.includeMode = data.include_mode === "any" ? "any" : "all";
        allRadio.checked = state.includeMode === "all";
        anyRadio.checked = !allRadio.checked;
        renderFilters();
        renderTags();
        refreshResults();
    }
    const presetActions = el("div", { class: "pld-preset-actions" },
        presetSelect, btn("Load", applyPreset), btn("Save preset", savePreset),
        btn("Clear filters", () => {
            for (const f of state.filters) { f.enabled = false; f.value = ""; }
            state.include = []; state.exclude = []; state.includeMode = "all";
            allRadio.checked = true; anyRadio.checked = false;
            renderFilters(); renderTags(); refreshResults();
        }));

    function setupDataset(data) {
        state.manifest = data.manifest;
        state.fields = data.fields || [];
        state.filters = state.fields.map((field) => ({
            field: field.key, enabled: false,
            operator: field.type === "number" ? "equals" : "contains", value: "",
        }));
        state.page = 0;
        state.matched = data.manifest.rows;
        state.total = data.manifest.rows;
        filterBox.hidden = false;
        tagBox.hidden = false;
        main.hidden = false;
        refreshPresets();
        renderFilters();
        renderTags();
        summary();
        queueSearch(true);
    }
    async function loadDataset(id) {
        state.datasetId = id;
        state.manifest = null;
        state.queryToken += 1;
        state.rows = [];
        rowsBox.replaceChildren();
        status("Loading dataset index…");
        try {
            const data = await request("/datasets/" + encodeURIComponent(id) + "/schema");
            if (state.closed || state.datasetId !== id) return;
            setupDataset(data);
        } catch (error) {
            filterBox.hidden = true;
            tagBox.hidden = true;
            main.hidden = true;
            summary();
            status(error.message, true);
        }
    }
    async function refreshDatasetList(selectId = null) {
        const response = await request("/datasets");
        state.datasets = response.datasets;
        const items = [[DEFAULT, "stablellama/erotic-image-prompts" +
            (response.datasets.some((d) => d.dataset_id === DEFAULT) ? "" : " (not imported)")]];
        for (const d of response.datasets) {
            if (d.dataset_id === DEFAULT) continue;
            items.push([d.dataset_id, d.name + " (" + d.rows.toLocaleString() + ")"]);
        }
        if (selectId) state.datasetId = selectId;
        setOptions(sourceSelect, items, state.datasetId);
        if (!items.some(([value]) => value === state.datasetId)) {
            state.datasetId = DEFAULT;
            sourceSelect.value = DEFAULT;
        }
        if (response.datasets.some((d) => d.dataset_id === state.datasetId)) {
            await loadDataset(state.datasetId);
        } else {
            state.manifest = null;
            state.total = 0;
            state.matched = 0;
            main.hidden = true;
            filterBox.hidden = true;
            tagBox.hidden = true;
            status("Dataset not imported. Click Import CSV to get started.");
            summary();
        }
    }
    async function importHF(format) {
        if (state.busy) return;
        state.busy = true;
        status("Importing Hugging Face " + format.toUpperCase() + " (download and indexing)…");
        try {
            const response = await post("/datasets/import", { format });
            state.selected.clear();
            await refreshDatasetList(response.dataset.dataset_id);
        } catch (error) {
            status(error.message, true);
        } finally { state.busy = false; }
    }
    async function importFile(file) {
        if (!file || state.busy) return;
        state.busy = true;
        status("Uploading and indexing " + file.name + "…");
        try {
            const data = new FormData();
            data.append("file", file, file.name);
            const response = await request("/datasets/upload", { method: "POST", body: data });
            state.selected.clear();
            await refreshDatasetList(response.dataset.dataset_id);
        } catch (error) {
            status(error.message, true);
        } finally { state.busy = false; fileInput.value = ""; }
    }
    fileInput.addEventListener("change", () => void importFile(fileInput.files?.[0]));
    sourceSelect.addEventListener("change", () => {
        state.selected.clear();
        void loadDataset(sourceSelect.value);
    });

    const previous = btn("← Previous", () => { if (state.page > 0) { state.page--; queueSearch(true); } });
    const next = btn("Next →", () => { state.page++; queueSearch(true); });
    const applyButton = btn("Apply Selection", () => void applySelection(), "pld-primary");
    function updateApplyButton() {
        applyButton.disabled = !state.manifest || state.busy ||
            (state.mode === "manual" && state.selected.size === 0);
        const qty = state.mode === "manual" ? state.selected.size
            : state.mode === "all_matches" ? state.matched
                : Math.min(state.matched, Number(nInput.value) || 0);
        applyButton.textContent = "Apply Selection (" + qty.toLocaleString() + ")";
    }
    modeSelect.addEventListener("change", () => { state.mode = modeSelect.value; summary(); });
    nInput.addEventListener("input", summary);
    seedInput.addEventListener("input", summary);

    async function applySelection() {
        if (state.busy || !state.manifest) return;
        state.busy = true;
        updateApplyButton();
        status("Preparing frozen prompt selection…");
        try {
            const selected = await post("/datasets/" + encodeURIComponent(state.datasetId) + "/select", {
                ...filtersPayload(), mode: state.mode, ids: [...state.selected],
                sample_size: Number(nInput.value), seed: Number(seedInput.value),
            });
            const target = widget(node, "selection_json");
            if (!target) throw new Error("Missing hidden selection widget");
            target.value = JSON.stringify(selected.snapshot);
            target.serialize = true;
            target.options = { ...target.options, serialize: true };
            const name = widget(node, "dataset_name");
            if (name) name.value = state.manifest.name;
            node._pldLastRun = undefined;
            node._pldqOnSelectionApplied?.();
            updateLabel(node);
            node.setDirtyCanvas?.(true, true);
            app.graph?.setDirtyCanvas?.(true, true);
            close();
        } catch (error) {
            status(error.message, true);
        } finally { state.busy = false; if (!state.closed) updateApplyButton(); }
    }

    left.append(
        el("h3", { text: "Dataset" }),
        sourceSelect,
        el("div", { class: "pld-imports" },
            btn("Import HF CSV", () => void importHF("csv")),
            btn("Import HF Parquet", () => void importHF("parquet")),
            btn("Upload CSV/Parquet", () => fileInput.click()), fileInput),
        el("p", { class: "pld-muted", text: "CSV requires no extra packages. Parquet needs pyarrow. Dataset indexes live outside the node repository." }),
        el("h3", { text: "Filters" }), filterBox,
        el("h3", { text: "Caption search" }), tagBox,
        el("h3", { text: "Saved filter presets" }), presetActions
    );
    main.append(
        el("div", { class: "pld-main-head" },
            el("h3", { text: "Matching prompts" }),
            btn("Check page", () => {
                for (const row of state.rows) if (state.selected.size < MAX_SELECTION) state.selected.add(row.id);
                renderRows(); summary();
            }),
            btn("Clear checked", () => { state.selected.clear(); renderRows(); summary(); })
        ), rowsBox,
        el("div", { class: "pld-pagination" }, previous, pageText, next));
    footer.append(
        el("div", { class: "pld-execution" },
            formRow("Apply mode", modeSelect),
            formRow("N", nInput), formRow("Seed", seedInput)),
        el("div", { class: "pld-footer-buttons" }, btn("Cancel", close), applyButton));
    modal.append(
        el("header", { class: "pld-header" },
            el("div", {}, el("h2", { text: "📚 Dataset Prompt Browser" }), message, stats),
            btn("×", close, "pld-close")),
        el("div", { class: "pld-content" }, left, main),
        footer
    );
    overlay.append(modal);
    document.body.append(overlay);
    summary();
    void refreshDatasetList().catch((error) => status(error.message, true));
    modal.focus();
}

const styleId = "comfy-prompt-dataset-css";
if (!document.getElementById(styleId)) {
    document.head.append(el("link", {
        id: styleId, rel: "stylesheet", href: new URL("./dataset_browser.css", import.meta.url).href,
    }));
}

app.registerExtension({
    name: "markwelshboy.PromptLibraryDatasetBrowser",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "PromptDatasetBrowserNode" && nodeData.name !== "PromptDatasetQueueNode") return;
        const previous = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = previous?.apply(this, args);
            const hidden = widget(this, "selection_json");
            if (hidden) {
                hidden.serialize = true;
                hidden.options = { ...hidden.options, serialize: true };
                hidden.computeSize = () => [0, -4];
                hidden.hidden = true;
            }
            const control = this.addWidget("button", "📚 Open Dataset Browser", null, () => openBrowser(this));
            control._pldButton = true;
            updateLabel(this);
            this.setSize([Math.max(340, this.size[0]), this.computeSize()[1]]);
            return result;
        };
        const oldExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (event) {
            oldExecuted?.apply(this, arguments);
            const receipt = event?.prompt_dataset?.[0];
            const queueReceipt = event?.prompt_dataset_queue?.[0];
            if (Number.isInteger(receipt?.count)) this._pldLastRun = receipt.count;
            if (Number.isInteger(queueReceipt?.index)) this._pldLastRun = queueReceipt.index + 1;
            updateLabel(this);
        };
    },
});
