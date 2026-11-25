// Minimal client: submit forms, stream job events over SSE, then open the result.
(function () {
  const live = document.getElementById("live");
  const trace = document.getElementById("trace");
  const status = document.getElementById("live-status");

  function addEvent(kind, message) {
    const li = document.createElement("li");
    li.className = "ev-" + kind;
    const k = document.createElement("span");
    k.className = "kind";
    k.textContent = kind;
    li.appendChild(k);
    li.appendChild(document.createTextNode(" " + message));
    trace.appendChild(li);
    trace.scrollTop = trace.scrollHeight;
  }

  function follow(jobId, label, onEnd) {
    const es = new EventSource("/api/jobs/" + jobId + "/events");
    es.onmessage = (e) => { const d = JSON.parse(e.data); addEvent(d.kind, (label ? label + ": " : "") + d.message); };
    es.addEventListener("end", (e) => { es.close(); onEnd(JSON.parse(e.data)); });
    es.onerror = () => { es.close(); onEnd({ status: "error", error: "connection lost" }); };
  }

  async function post(url, form) {
    const res = await fetch(url, { method: "POST", body: new FormData(form) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Request failed (" + res.status + ")");
    return body;
  }

  function begin(btn) {
    live.classList.remove("hidden");
    trace.innerHTML = "";
    status.textContent = "running";
    status.className = "pill";
    btn.disabled = true;
  }

  const single = document.getElementById("screen-form");
  single && single.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = single.querySelector("button");
    begin(btn);
    try {
      const { job_id } = await post("/api/screenings", single);
      follow(job_id, "", (end) => {
        btn.disabled = false;
        if (end.status === "done") { status.textContent = "done"; status.className = "pill pill-ok"; location.href = "/screenings/" + end.result_id; }
        else { status.textContent = "failed"; status.className = "pill pill-warn"; addEvent("error", end.error || "failed"); }
      });
    } catch (err) { btn.disabled = false; addEvent("error", err.message); status.textContent = "failed"; }
  });

  const demoBtn = document.getElementById("demo-btn");
  demoBtn && demoBtn.addEventListener("click", async () => {
    begin(demoBtn);
    const fd = new FormData();
    fd.append("thesis", single.querySelector("select[name=thesis]").value);
    try {
      const res = await fetch("/api/demo", { method: "POST", body: fd });
      const { job_id } = await res.json();
      addEvent("step", "Offline sample: fictional company, scripted model responses");
      follow(job_id, "", (end) => { demoBtn.disabled = false; if (end.status === "done") location.href = "/screenings/" + end.result_id; else addEvent("error", end.error || "failed"); });
    } catch (err) { demoBtn.disabled = false; addEvent("error", err.message); }
  });

  const batch = document.getElementById("batch-form");
  batch && batch.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = batch.querySelector("button");
    begin(btn);
    try {
      const { job_ids } = await post("/api/batch", batch);
      let remaining = job_ids.length;
      addEvent("step", "Queued " + remaining + " screenings");
      job_ids.forEach((id) => fetch("/api/jobs/" + id).then((r) => r.json()).then((j) =>
        follow(id, j.label, () => { remaining -= 1; if (remaining === 0) { status.textContent = "done"; location.reload(); } })));
    } catch (err) { btn.disabled = false; addEvent("error", err.message); status.textContent = "failed"; }
  });
})();
