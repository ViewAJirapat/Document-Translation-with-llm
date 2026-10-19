// Page behaviour: llama-server status pill, live job list, and the side-by-side review editor.
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const ACTIVE = ["queued", "extracting", "translating"];

async function getJSON(url, options) {
  const r = await fetch(url, options);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || body.detail || `HTTP ${r.status}`);
  return body;
}

// --- llama-server status (every page) -------------------------------------------------------
(async () => {
  const pill = $("#server");
  try {
    const s = await getJSON("/api/server");
    pill.textContent = s.ok ? `llama: ${s.model}` : "llama-server offline";
    pill.className = "pill " + (s.ok ? "ok" : "bad");
    pill.title = s.ok ? "" : s.error;
  } catch {
    pill.textContent = "llama-server status unknown";
    pill.className = "pill bad";
  }
})();

$$("[data-time]").forEach((el) => {
  el.textContent = new Date(parseFloat(el.dataset.time) * 1000).toLocaleString();
});

// --- job list ---------------------------------------------------------------------------------
if ($("table.jobs")) {
  const tick = async () => {
    const jobs = await getJSON("/api/jobs").catch(() => null);
    if (!jobs) return setTimeout(tick, 5000);
    for (const j of jobs) {
      const row = $(`tr[data-job="${j.id}"]`);
      if (!row) continue;
      const badge = $('[data-f="status"]', row);
      badge.textContent = j.label;
      badge.className = `badge s-${j.status}`;
      $('[data-f="pages"]', row).textContent = j.pages || "–";
      $('[data-f="progress"]', row).textContent = j.total ? `${j.done} / ${j.total}` : "–";
    }
    if (jobs.some((j) => ACTIVE.includes(j.status))) setTimeout(tick, 3000);
  };
  tick();
}

// --- review editor ----------------------------------------------------------------------------
const jobEl = $("#job");
if (jobEl) {
  const jobId = jobEl.dataset.id;
  let since = parseFloat(jobEl.dataset.since);
  let status = jobEl.dataset.status;
  const dirty = new Set(); // textareas with unsaved edits

  const autosize = (ta) => {
    ta.style.height = "auto";
    ta.style.height = ta.scrollHeight + 2 + "px";
  };
  const setBlockStatus = (block, st) => {
    block.className = block.className.replace(/\bst-\S+/, "st-" + st);
  };
  const flash = (block, text, bad = false) => {
    let note = $(".note", block);
    if (!note) {
      note = document.createElement("div");
      note.className = "note";
      block.appendChild(note);
    }
    note.textContent = text;
    note.classList.toggle("bad", bad);
    clearTimeout(note.timer);
    if (!bad) note.timer = setTimeout(() => note.remove(), 1500);
  };

  async function save(ta) {
    if (!dirty.has(ta)) return;
    const block = ta.closest(".block");
    try {
      await getJSON(`/api/blocks/${block.dataset.block}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [ta.dataset.field]: ta.value }),
      });
      dirty.delete(ta);
      if (ta.dataset.field === "translation") setBlockStatus(block, "done");
      flash(block, "saved");
    } catch (e) {
      flash(block, "Not saved: " + e.message, true);
    }
  }

  $$(".block textarea").forEach((ta) => {
    autosize(ta);
    ta.addEventListener("input", () => {
      autosize(ta);
      dirty.add(ta);
    });
    ta.addEventListener("blur", () => save(ta));
  });
  window.addEventListener("resize", () => $$(".block textarea").forEach(autosize));
  window.addEventListener("beforeunload", (e) => {
    if (dirty.size) e.preventDefault();
  });

  $$(".retranslate").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const block = btn.closest(".block");
      const out = $('textarea[data-field="translation"]', block);
      await save($('textarea[data-field="source"]', block)); // translate the English as shown
      btn.disabled = true;
      btn.classList.add("spin");
      try {
        const r = await getJSON(`/api/blocks/${block.dataset.block}/retranslate`, { method: "POST" });
        out.value = r.translation;
        dirty.delete(out);
        autosize(out);
        setBlockStatus(block, "done");
      } catch (e) {
        flash(block, e.message, true);
      } finally {
        btn.disabled = false;
        btn.classList.remove("spin");
      }
    })
  );

  async function poll() {
    let j;
    try {
      j = await getJSON(`/api/jobs/${jobId}?since=${since}`);
    } catch {
      return setTimeout(poll, 5000);
    }
    const wasReading = status === "queued" || status === "extracting";
    since = j.now;
    status = j.status;
    $("#status").textContent = j.label;
    $("#status").className = `badge s-${j.status}`;
    $("#count").textContent = `${j.done} / ${j.total}`;
    $("#bar").style.width = j.total ? `${(100 * j.done) / j.total}%` : "0%";
    $("#pages").textContent = j.pages || "?";
    const err = $("#error");
    err.hidden = !j.error;
    err.textContent = j.error || "";
    for (const b of j.blocks) {
      const block = $(`.block[data-block="${b.id}"]`);
      const out = block && $('textarea[data-field="translation"]', block);
      if (!out || dirty.has(out) || document.activeElement === out) continue;
      out.value = b.translation || "";
      autosize(out);
      setBlockStatus(block, b.status);
    }
    const reading = status === "queued" || status === "extracting";
    if (wasReading && !reading) return location.reload(); // blocks exist now: render them
    if (ACTIVE.includes(status)) return setTimeout(poll, 2000);
    if (!dirty.size) location.reload(); // finished: show the buttons for the new state
  }
  if (ACTIVE.includes(status)) setTimeout(poll, 2000);
}
