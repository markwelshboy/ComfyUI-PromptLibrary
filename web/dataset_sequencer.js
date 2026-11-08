// ComfyUI dataset prompt sequencer. Inspired by shootthesound's MIT
// SequentialImageLoader queue UX; implemented independently.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const TYPE = "PromptDatasetQueueNode";
const widget = (n, name) => n.widgets?.find(w => w.name === name);
const allNodes = () => (app.graph?._nodes || []).filter(n => n.type === TYPE);
const btn = (text, cb) => {
    const e = document.createElement("button");
    e.type = "button"; e.textContent = text; e.onclick = cb; return e;
};
const div = (cls = "", text = "") => {
    const e = document.createElement("div");
    e.className = cls; e.textContent = text; return e;
};
function items(n) {
    try {
        const d = JSON.parse(widget(n, "selection_json")?.value || "{}");
        return Array.isArray(d.selected) ? d.selected : [];
    } catch { return []; }
}
function getIndex(n) { return Math.max(0, Math.trunc(Number(widget(n, "sequence_index")?.value) || 0)); }
function setIndex(n, value) {
    const w = widget(n, "sequence_index"), size = items(n).length;
    if (!w) return;
    w.value = size ? Math.max(0, Math.min(Math.trunc(value), size - 1)) : 0;
    w.callback?.(w.value);
    n.setDirtyCanvas?.(true, true);
    app.graph?.setDirtyCanvas?.(true, true);
    render(n);
}
function caption(n, item) {
    if (!item?.text) return "—";
    const search = String(widget(n, "replace_name")?.value || "").trim();
    const trigger = String(widget(n, "trigger_word")?.value || "").trim();
    if (!search || !trigger) return item.text;
    const pattern = search.replace(/[|\\{}()[\]^$+*?.]/g, "\\$&");
    return item.text.replace(new RegExp("(?<!\\w)" + pattern + "(?!\\w)", "gi"), () => trigger);
}
const short = (v) => {
    const t = String(v).replace(/\s+/g, " ").trim();
    return t.length > 210 ? t.slice(0, 210) + "…" : t;
};
function render(n) {
    const ui = n._pldqUI;
    if (!ui) return;
    const rows = items(n), total = rows.length;
    const position = Math.min(getIndex(n), Math.max(0, total - 1));
    const ran = Number.isInteger(n._pldqRanIndex) && n._pldqRanIndex < total ? n._pldqRanIndex : null;
    const lastText = ran === null ? "—" : caption(n, rows[ran]);
    const nextText = total ? caption(n, rows[position]) : "Open Browser and Apply Selection first.";
    ui.status.textContent = total ? (n._pldqHold ? "⏸ Holding " : "Next: ") +
        (position + 1) + " / " + total : "No prompts selected";
    ui.scrub.max = String(Math.max(0, total - 1));
    ui.scrub.value = String(position);
    ui.scrub.disabled = !total || !!n._pldqHold || !!n._pldqAuto;
    ui.current.textContent = short(lastText); ui.current.title = lastText;
    ui.next.textContent = short(nextText); ui.next.title = nextText;
    ui.queue.textContent = n._pldqAuto ? "■ Stop queueing" : "▶ Queue remaining";
    ui.queue.disabled = !total || !!n._pldqHold;
    for (const b of [ui.reset, ui.prev, ui.nextBtn]) b.disabled = !total || !!n._pldqHold || !!n._pldqAuto;
    ui.holdLast.textContent = n._pldqHold && n._pldqHoldType === "last" ? "▶ Resume" : "⏸ Hold last";
    ui.holdNext.textContent = n._pldqHold && n._pldqHoldType === "next" ? "▶ Resume" : "⏸ Hold next";
    ui.holdLast.disabled = !total || (!!n._pldqAuto && !n._pldqHold);
    ui.holdNext.disabled = !total || (!!n._pldqAuto && !n._pldqHold);
    ui.progress.textContent = ran === null ? "Not run" : "Last: " + (ran + 1) + "/" + total;
}
function stop(n) { n._pldqAuto = false; render(n); }
function stopAll() {
    for (const n of allNodes()) {
        if (n._pldqAuto) stop(n);
        n._pldqReceiptPending = false;
    }
}
function toggleHold(n, type) {
    if (!items(n).length || n._pldqAuto) return;
    if (n._pldqHold) {
        n._pldqHold = false;
        n._pldqHoldType = null;
        if (Number.isInteger(n._pldqResume)) setIndex(n, n._pldqResume);
    } else {
        n._pldqResume = getIndex(n);
        n._pldqHold = true;
        n._pldqHoldType = type;
        if (type === "last" && Number.isInteger(n._pldqRanIndex)) setIndex(n, n._pldqRanIndex);
    }
    render(n);
}
async function enqueue(n) {
    try { await app.queuePrompt(0, 1); }
    catch (err) { console.warn("[PromptDatasetQueue] Queue failed", err); stop(n); }
}
function installQueueHook() {
    if (app._pldqQueueHook || typeof app.queuePrompt !== "function") return;
    const original = app.queuePrompt.bind(app);
    app.queuePrompt = async function (...args) {
        const result = await original(...args);
        // AFTER async serialization. The just-submitted job uses the old index.
        for (const n of allNodes()) {
            if (n.mode === 2 || n.mode === 4 || n._pldqHold) continue;
            const total = items(n).length;
            if (total) setIndex(n, (getIndex(n) + 1) % total);
        }
        return result;
    };
    app._pldqQueueHook = true;
}
function installEvents() {
    if (app._pldqEvents) return;
    api.addEventListener("execution_success", () => {
        let again = null;
        for (const n of allNodes()) {
            if (!n._pldqAuto || !n._pldqReceiptPending) continue;
            n._pldqReceiptPending = false;
            if (n._pldqRanIndex >= n._pldqRanTotal - 1) {
                stop(n);
                app.extensionManager?.toast?.add?.({
                    severity: "success", summary: "Dataset Prompt Queue",
                    detail: "Completed " + n._pldqRanTotal + " prompts.", life: 4500,
                });
            } else {
                again = n;
            }
        }
        if (again) setTimeout(() => { if (again._pldqAuto) void enqueue(again); }, 0);
    });
    api.addEventListener("execution_error", stopAll);
    api.addEventListener("execution_interrupted", stopAll);
    app._pldqEvents = true;
}
function attach(n) {
    if (typeof n.addDOMWidget !== "function") {
        console.warn("[PromptDatasetQueue] DOM widgets unavailable in this ComfyUI frontend");
        return;
    }
    const panel = document.createElement("section"); panel.className = "pldq-panel";
    const status = div("pldq-status");
    const scrub = document.createElement("input");
    scrub.type = "range"; scrub.min = "0"; scrub.max = "0"; scrub.step = "1";
    scrub.oninput = (e) => setIndex(n, Number(e.target.value));
    const reset = btn("⏮ Reset", () => setIndex(n, 0));
    const prev = btn("‹ Prev", () => setIndex(n, getIndex(n) - 1));
    const nextBtn = btn("Next ›", () => setIndex(n, getIndex(n) + 1));
    const holdLast = btn("⏸ Hold last", () => toggleHold(n, "last"));
    const holdNext = btn("⏸ Hold next", () => toggleHold(n, "next"));
    const progress = div("pldq-progress");
    const queue = btn("▶ Queue remaining", () => {
        if (n._pldqAuto) { stop(n); return; }
        if (!items(n).length || n._pldqHold) return;
        // Only one queue controller may operate the same shared graph.
        for (const other of allNodes()) if (other !== n) stop(other);
        n._pldqAuto = true; n._pldqReceiptPending = false;
        render(n); void enqueue(n);
    });
    const current = div("pldq-preview"), next = div("pldq-preview");
    function row(...elements) {
        const wrapper = div("pldq-row");
        wrapper.append(...elements);
        return wrapper;
    }
    function preview(label, value) {
        const wrapper = div("pldq-box");
        const heading = document.createElement("strong"); heading.textContent = label;
        wrapper.append(heading, value);
        return wrapper;
    }
    panel.append(status, scrub,
        row(reset, prev, nextBtn),
        row(holdLast, holdNext),
        row(queue, progress),
        row(preview("Current", current), preview("Next", next)));
    n._pldqUI = {status, scrub, reset, prev, nextBtn, holdLast, holdNext, queue, progress, current, next};
    n.addDOMWidget("pldq_controls", "pldq_controls", panel, {
        serialize: false, hideOnZoom: false, getMinHeight: () => 250, getMaxHeight: () => 290,
    });
    n._pldqOnSelectionApplied = () => {
        stop(n);
        n._pldqHold = false; n._pldqHoldType = null;
        n._pldqRanIndex = null; n._pldqRanTotal = null;
        n._pldqReceiptPending = false;
        setIndex(n, 0);
    };
    render(n);
    n.setSize([Math.max(350, n.size[0]), Math.max(525, n.computeSize()[1])]);
}
const stylesheet = "pldq-style";
if (!document.getElementById(stylesheet)) {
    const link = document.createElement("link");
    link.id = stylesheet; link.rel = "stylesheet";
    link.href = new URL("./dataset_sequencer.css", import.meta.url).href;
    document.head.append(link);
}
app.registerExtension({
    name: "markwelshboy.DatasetPromptQueue",
    async setup() { installQueueHook(); installEvents(); },
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== TYPE) return;
        const originalCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function (...args) {
            const result = originalCreated?.apply(this, args);
            const index = widget(this, "sequence_index");
            if (index) {
                index.serialize = true;
                index.options = { ...index.options, serialize: true };
                index.hidden = true;
                index.computeSize = () => [0, -4];
            }
            attach(this);
            return result;
        };
        const configured = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (...args) {
            const result = configured?.apply(this, args);
            setTimeout(() => render(this), 0);
            return result;
        };
        const executed = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            executed?.apply(this, arguments);
            const info = message?.prompt_dataset_queue?.[0];
            if (!Number.isInteger(info?.index) || !Number.isInteger(info?.total)) return;
            this._pldqRanIndex = info.index;
            this._pldqRanTotal = info.total;
            this._pldqReceiptPending = true;
            render(this);
        };
        const removed = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function (...args) {
            stop(this);
            this._pldqOnSelectionApplied = null;
            return removed?.apply(this, args);
        };
    },
});
