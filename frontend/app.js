(() => {
  "use strict";

  const APP = document.getElementById("app");
  const STORAGE_KEY = "wire.currentAgentId";
  const POLL_MS = 15000;

  const ICONS = {
    envelope: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.5 6.5 12 13l8.5-6.5"/></svg>',
    flower: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="2.6"/><path d="M12 9.4c-1.6 0-3-1.3-3-3.4s1.4-3.2 3-3.2 3 1.1 3 3.2-1.4 3.4-3 3.4Z"/><path d="M12 14.6c1.6 0 3 1.3 3 3.4s-1.4 3.2-3 3.2-3-1.1-3-3.2 1.4-3.4 3-3.4Z"/><path d="M14.6 12c0-1.6 1.3-3 3.4-3s3.2 1.4 3.2 3-1.1 3-3.2 3-3.4-1.4-3.4-3Z"/><path d="M9.4 12c0 1.6-1.3 3-3.4 3S2.8 13.6 2.8 12s1.1-3 3.2-3 3.4 1.4 3.4 3Z"/></svg>',
    typewriter: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="12" width="18" height="7" rx="1.5"/><path d="M5 12V8a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v4"/><path d="M9 16h6"/><circle cx="8" cy="9" r="0.8" fill="currentColor" stroke="none"/><circle cx="12" cy="9" r="0.8" fill="currentColor" stroke="none"/><circle cx="16" cy="9" r="0.8" fill="currentColor" stroke="none"/></svg>',
    message: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 5.5h16v10H9.5L5 19v-3.5H4z"/></svg>',
  };

  const PIPELINE_STAGES = [
    { key: "discover", label: "Discover", tone: "clay", icon: ICONS.envelope },
    { key: "evaluate", label: "Evaluate", tone: "sage", icon: ICONS.flower },
    { key: "write", label: "Write", tone: "plum", icon: ICONS.typewriter },
    { key: "publish", label: "Publish", tone: "blush", icon: ICONS.message },
  ];

  let pollTimer = null;
  let geminiConfigured = null;

  // ---------- fetch helpers ----------

  async function apiGet(path) {
    const res = await fetch(path);
    let body = null;
    try { body = await res.json(); } catch (_) { /* no body */ }
    if (!res.ok) {
      const message = body && body.error && body.error.message
        ? body.error.message
        : `Request failed (${res.status})`;
      const err = new Error(message);
      err.status = res.status;
      throw err;
    }
    return body;
  }

  async function apiPost(path, payload) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    let body = null;
    try { body = await res.json(); } catch (_) { /* no body */ }
    if (!res.ok) {
      const message = body && body.error && body.error.message
        ? body.error.message
        : `Request failed (${res.status})`;
      const err = new Error(message);
      err.status = res.status;
      throw err;
    }
    return body;
  }

  async function apiPatch(path, payload) {
    const res = await fetch(path, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    let body = null;
    try { body = await res.json(); } catch (_) { /* no body */ }
    if (!res.ok) {
      const message = body && body.error && body.error.message
        ? body.error.message
        : `Request failed (${res.status})`;
      const err = new Error(message);
      err.status = res.status;
      throw err;
    }
    return body;
  }

  async function apiDelete(path) {
    const res = await fetch(path, { method: "DELETE" });
    let body = null;
    try { body = await res.json(); } catch (_) { /* no body */ }
    if (!res.ok) {
      const message = body && body.error && body.error.message
        ? body.error.message
        : `Request failed (${res.status})`;
      const err = new Error(message);
      err.status = res.status;
      throw err;
    }
    return body;
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) {
      Object.entries(attrs).forEach(([k, v]) => {
        if (k === "class") node.className = v;
        else if (k === "html") node.innerHTML = v;
        else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
        else node.setAttribute(k, v);
      });
    }
    (children || []).forEach((c) => {
      if (c === null || c === undefined) return;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    });
    return node;
  }

  function fmtTime(iso) {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return d.toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      });
    } catch (_) {
      return iso;
    }
  }

  function getCurrentAgentId() {
    return localStorage.getItem(STORAGE_KEY);
  }

  function setCurrentAgentId(id) {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  }

  function parseTopics(raw) {
    return (raw || "")
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
  }

  // ---------- setup view ----------

  function renderSetup(prefill, errorMsg) {
    stopPolling();
    const values = prefill || {
      name: "Ada",
      domain: "AI Security",
      voice: "skeptical, technical, punchy, and specific",
      stance: "distrusts benchmark hype, cares about supply-chain and model-weight risk",
      formatting: "2-4 short paragraphs, no emoji, ends with a pointed question",
      topics: "",
    };

    const inputs = {};
    const field = (key, label, hint, isTextarea) => {
      const input = el(isTextarea ? "textarea" : "input", {
        id: `f-${key}`,
        value: isTextarea ? undefined : values[key],
      });
      if (isTextarea) input.value = values[key];
      inputs[key] = input;
      return el("div", { class: "field" }, [
        el("label", { for: `f-${key}` }, [label]),
        input,
        hint ? el("div", { class: "hint" }, [hint]) : null,
      ]);
    };

    const errorNode = errorMsg ? el("div", { class: "form-error" }, [errorMsg]) : null;
    const submitBtn = el("button", { class: "btn btn-primary", type: "submit" }, ["Plant persona"]);

    const form = el("form", {
      onsubmit: async (e) => {
        e.preventDefault();
        submitBtn.disabled = true;
        submitBtn.textContent = "Planting…";
        try {
          const persona = {
            name: inputs.name.value.trim() || "Ada",
            domain: inputs.domain.value.trim() || "AI Research",
            voice: inputs.voice.value.trim(),
            stance: inputs.stance.value.trim(),
            formatting: inputs.formatting.value.trim(),
            topics: parseTopics(inputs.topics.value),
          };
          const res = await apiPost("/api/agent/init", { persona });
          setCurrentAgentId(res.agentId);
          await renderDashboard(res.agentId);
        } catch (err) {
          submitBtn.disabled = false;
          submitBtn.textContent = "Plant persona";
          renderSetup(values, err.message || "Could not create persona. Try again.");
        }
      },
    }, [
      field("name", "Persona name", "How the byline will read."),
      field("domain", "Domain / beat", "What the persona covers, e.g. \"AI Security\"."),
      field("topics", "Topics / interests", "Comma-separated, optional — refines the beat, e.g. \"prompt injection, red teaming\"."),
      field("voice", "Voice", "Tone in a few words.", true),
      field("stance", "Editorial stance", "What it's skeptical of, what it values.", true),
      field("formatting", "Formatting rules", "Length, structure, sign-off style.", true),
      el("div", { class: "setup-actions" }, [submitBtn, errorNode]),
    ]);

    const existingLink = getCurrentAgentId()
      ? el("div", { style: "margin-top:22px" }, [
          el("button", {
            class: "btn",
            onclick: () => renderDashboard(getCurrentAgentId()),
          }, ["← Back to current persona"]),
        ])
      : null;

    APP.replaceChildren(
      el("div", { class: "setup-wrap" }, [
        el("div", { class: "setup-eyebrow" }, ["Plant a new persona"]),
        el("h1", {}, ["Set the editorial line."]),
        el("p", { class: "lede" }, [
          "This persona will run on its own: discovering technical stories from Hacker News and arXiv, judging which ones are worth growing, writing them up in the given voice, and sending finished letters to its feed on a fixed interval.",
        ]),
        form,
        existingLink,
      ])
    );
  }

  // ---------- dashboard view ----------

  async function renderDashboard(agentId, editing) {
    stopPolling();
    setCurrentAgentId(agentId);
    APP.replaceChildren(loadingShell());

    let persona, feed, rejected, agentsList, health, memory, pipelineCounts;
    try {
      [persona, feed, rejected, agentsList, health, memory, pipelineCounts] = await Promise.all([
        apiGet(`/api/agent/persona?agentId=${encodeURIComponent(agentId)}`),
        apiGet(`/api/agent/feed?agentId=${encodeURIComponent(agentId)}`),
        apiGet(`/api/agent/rejected?agentId=${encodeURIComponent(agentId)}`),
        apiGet("/api/agent/list"),
        apiGet("/api/health"),
        apiGet(`/api/agent/memory?agentId=${encodeURIComponent(agentId)}`),
        apiGet(`/api/agent/pipeline/counts?agentId=${encodeURIComponent(agentId)}`),
      ]);
    } catch (err) {
      if (err.status === 404) {
        setCurrentAgentId(null);
        renderSetup(null, "That persona no longer exists. Set up a new one below.");
        return;
      }
      renderErrorShell(err.message, () => renderDashboard(agentId));
      return;
    }

    geminiConfigured = health.geminiConfigured;
    history.replaceState({}, "", `/persona/${encodeURIComponent(agentId)}`);
    paintDashboard(agentId, persona, feed, rejected, agentsList, memory, !!editing, pipelineCounts);
    if (!editing) startPolling(agentId);
  }

  function loadingShell() {
    return el("div", { class: "loading-state", style: "margin-top:60px" }, ["Loading persona…"]);
  }

  function renderErrorShell(message, retry) {
    stopPolling();
    APP.replaceChildren(
      el("div", { class: "error-state", style: "margin-top:60px" }, [
        el("div", {}, [`Something went wrong: ${message}`]),
        el("button", { class: "btn", style: "margin-top:14px", onclick: retry }, ["Retry"]),
      ])
    );
  }

  function paintDashboard(agentId, persona, feed, rejected, agentsList, memory, editing, pipelineCounts) {
    const p = persona.persona || {};
    const posts = (feed && feed.posts) || [];
    const spikes = (rejected && rejected.rejected) || [];
    const memories = (memory && memory.memory) || [];

    // top bar
    const switcher = el("select", {
      onchange: (e) => renderDashboard(e.target.value),
    }, agentsList.agents.map((a) =>
      el("option", { value: a.id, selected: a.id === agentId ? "selected" : undefined }, [
        `${a.persona.name || "Untitled"} · ${a.persona.domain || ""}`,
      ])
    ));

    const topbar = el("div", { class: "topbar" }, [
      el("div", { class: "brand" }, [
        el("span", { class: "brand-mark", html: ICONS.flower }, []),
        "Greenhouse",
      ]),
      el("div", { class: "agent-switcher" }, [
        agentsList.agents.length > 1 ? switcher : null,
        el("button", { class: "btn", onclick: () => renderSetup() }, ["+ New persona"]),
      ]),
    ]);

    const mastheadBody = editing
      ? renderEditForm(agentId, p, () => renderDashboard(agentId, false))
      : renderMastheadReadout(agentId, persona, p, pipelineCounts);

    const masthead = el("div", { class: "masthead" }, [
      el("div", { class: "masthead-eyebrow" }, [
        el("span", { class: "status-dot" }, []),
        editing ? "Editing persona" : "In bloom · writing on its own schedule",
      ]),
      mastheadBody,
    ]);

    const feedSection = el("div", {}, [
      el("div", { class: "section-title" }, ["Letters sent"]),
      posts.length === 0
        ? el("div", { class: "empty-state" }, [
            "No letters yet. The persona discovers, judges, and writes on its own schedule — check back after the next cycle, or watch this page refresh automatically.",
          ])
        : el("div", { class: "feed" }, posts.map(renderClipping)),
    ]);

    const spikeSection = el("div", {}, [
      el("div", { class: "section-title" }, ["The compost heap", el("span", {}, [`${spikes.length}`])]),
      el("div", { class: "spike-panel" },
        spikes.length === 0
          ? [el("div", { style: "color:var(--ink-faint);font-size:12.5px;line-height:1.6" }, [
              "Nothing composted yet. Rejected candidates and the editorial reason will show up here.",
            ])]
          : spikes.map((s) =>
              el("div", { class: "spike-item" }, [
                el("div", { class: "headline" }, [s.title]),
                el("div", { class: "reason" }, [s.reason]),
              ])
            )
      ),
    ]);

    const memorySection = el("div", { style: "margin-top:30px" }, [
      el("div", { class: "section-title" }, ["What it remembers", el("span", {}, [`${memories.length}`])]),
      el("div", { class: "spike-panel" },
        memories.length === 0
          ? [el("div", { style: "color:var(--ink-faint);font-size:12.5px;line-height:1.6" }, [
              "No history yet. Once this persona publishes, its own recent posts show up here — this is exactly what its writer prompt sees to avoid repeating itself.",
            ])]
          : memories.map((m) =>
              el("div", { class: "spike-item" }, [
                el("div", { class: "headline", style: "text-decoration:none;color:var(--sage-deep)" }, [m.title]),
                el("div", { class: "reason" }, [m.summary]),
              ])
            )
      ),
    ]);

    const grid = el("div", { class: "main-grid" }, [
      feedSection,
      el("div", {}, [spikeSection, memorySection]),
    ]);

    APP.replaceChildren(
      topbar,
      masthead,
      grid,
      el("div", { class: "footer-note" }, [
        "Refreshes on its own. Discovery pulls from Hacker News and arXiv — judging and writing run on Gemini.",
      ])
    );
  }

  function stageCount(counts, stage) {
    if (!counts) return null;
    if (stage === "evaluate") {
      const e = counts.evaluate || {};
      return (e.accepted || 0) + (e.rejected || 0) + (e.failed || 0) + (e.skipped || 0);
    }
    return counts[stage] || 0;
  }

  function renderQuotaBanner(pipelineCounts) {
    if (!pipelineCounts) return null;
    const used = pipelineCounts.llmCallsUsedToday;
    const budget = pipelineCounts.llmDailyCallBudget;
    if (typeof used !== "number" || typeof budget !== "number") return null;
    const exhausted = used >= budget;
    return el("div", { class: exhausted ? "config-warning" : "quota-note" }, [
      exhausted
        ? `Gemini call budget used up for today (${used}/${budget}, shared across personas). Judging and writing are paused until the budget resets or is raised.`
        : `Gemini calls used today: ${used}/${budget} (shared across personas).`,
    ]);
  }

  function renderMastheadReadout(agentId, persona, p, pipelineCounts) {
    const topicsLine = (p.topics && p.topics.length)
      ? el("div", {}, [el("b", {}, ["Topics: "]), p.topics.join(", ")])
      : null;

    return el("div", {}, [
      el("h1", {}, [p.name || "Untitled Persona"]),
      el("div", { class: "byline" }, [
        el("div", {}, [el("b", {}, ["Beat: "]), p.domain || "—"]),
        topicsLine,
        el("div", {}, [el("b", {}, ["Voice: "]), p.voice || "—"]),
        el("div", {}, [el("b", {}, ["Stance: "]), p.stance || "—"]),
      ]),
      el("div", { class: "masthead-stats" }, [
        stat(persona.postCount || 0, "Sent"),
        stat(persona.rejectedCount || 0, "Composted"),
        stat(fmtTime(persona.updatedAt), "Last activity"),
      ]),
      !geminiConfigured
        ? el("div", { class: "config-warning" }, [
            "GEMINI_API_KEY is not configured on the server. Judging and writing will fail and this persona will not publish until it's set.",
          ])
        : null,
      renderQuotaBanner(pipelineCounts),
      el("div", { class: "masthead-actions" }, [
        el("button", { class: "btn", onclick: () => renderDashboard(agentId, true) }, ["Edit persona"]),
        el("button", { class: "btn btn-danger", onclick: () => archivePersona(agentId) }, ["Archive persona"]),
      ]),
      el("div", { class: "pipeline" }, PIPELINE_STAGES.map((s) => {
        const count = stageCount(pipelineCounts, s.key);
        return el("button", {
          type: "button",
          class: `stage tone-${s.tone}`,
          onclick: () => goToPipelineStage(agentId, s.key),
        }, [
          el("span", { html: s.icon }, []),
          el("span", { class: "label" }, [s.label]),
          el("span", { class: "badge" }, [count === null ? "—" : String(count)]),
        ]);
      })),
    ]);
  }

  function renderEditForm(agentId, p, onCancel) {
    const values = {
      name: p.name || "",
      domain: p.domain || "",
      topics: (p.topics || []).join(", "),
      voice: p.voice || "",
      stance: p.stance || "",
      formatting: p.formatting || "",
    };

    const inputs = {};
    const field = (key, label, hint, isTextarea) => {
      const input = el(isTextarea ? "textarea" : "input", { id: `e-${key}` });
      input.value = values[key];
      inputs[key] = input;
      return el("div", { class: "field" }, [
        el("label", { for: `e-${key}` }, [label]),
        input,
        hint ? el("div", { class: "hint" }, [hint]) : null,
      ]);
    };

    const errorHolder = el("div", {}, []);
    const saveBtn = el("button", { class: "btn btn-primary", type: "submit" }, ["Save changes"]);
    const cancelBtn = el("button", { class: "btn", type: "button", onclick: onCancel }, ["Cancel"]);

    const form = el("form", {
      onsubmit: async (e) => {
        e.preventDefault();
        saveBtn.disabled = true;
        saveBtn.textContent = "Saving…";
        errorHolder.replaceChildren();
        try {
          await apiPatch(`/api/agent/persona?agentId=${encodeURIComponent(agentId)}`, {
            name: inputs.name.value.trim() || p.name,
            domain: inputs.domain.value.trim() || p.domain,
            topics: parseTopics(inputs.topics.value),
            voice: inputs.voice.value.trim(),
            stance: inputs.stance.value.trim(),
            formatting: inputs.formatting.value.trim(),
          });
          await renderDashboard(agentId, false);
        } catch (err) {
          saveBtn.disabled = false;
          saveBtn.textContent = "Save changes";
          errorHolder.replaceChildren(
            el("div", { class: "form-error" }, [err.message || "Could not save changes. Try again."])
          );
        }
      },
    }, [
      field("name", "Persona name"),
      field("domain", "Domain / beat"),
      field("topics", "Topics / interests", "Comma-separated."),
      field("voice", "Voice", null, true),
      field("stance", "Editorial stance", null, true),
      field("formatting", "Formatting rules", null, true),
      el("div", { class: "setup-actions" }, [saveBtn, cancelBtn, errorHolder]),
    ]);

    return form;
  }

  async function archivePersona(agentId) {
    const ok = window.confirm(
      "Archive this persona? It stops writing on its own schedule. Its past letters and history are kept, not deleted, and it can still be reached by URL — it just drops off the persona list."
    );
    if (!ok) return;
    try {
      await apiDelete(`/api/agent/persona?agentId=${encodeURIComponent(agentId)}`);
    } catch (err) {
      window.alert(err.message || "Could not archive this persona. Try again.");
      return;
    }
    setCurrentAgentId(null);
    let agentsList;
    try {
      agentsList = await apiGet("/api/agent/list");
    } catch (_) {
      renderSetup();
      return;
    }
    if (agentsList.agents.length > 0) {
      renderDashboard(agentsList.agents[0].id);
    } else {
      renderSetup();
    }
  }

  function stat(value, label) {
    return el("div", { class: "stat" }, [
      el("span", { class: "num" }, [String(value)]),
      el("span", { class: "label" }, [label]),
    ]);
  }

  function renderClipping(post) {
    return el("div", { class: "clipping" }, [
      el("div", { class: "dateline" }, [fmtTime(post.createdAt)]),
      el("p", { class: "body-text" }, [post.text || ""]),
      post.rationale ? el("p", { class: "rationale" }, [post.rationale]) : null,
      post.sources && post.sources.length
        ? el("div", { class: "sources" }, post.sources.map((src) =>
            el("a", { href: src, target: "_blank", rel: "noopener noreferrer" }, [src])
          ))
        : null,
    ]);
  }

  // ---------- pipeline stage pages ----------

  const STAGE_STATUS_LABEL = {
    found: "Found", accepted: "Accepted", rejected: "Rejected", failed: "Failed",
    skipped: "Skipped (quota)", drafted: "Drafted", published: "Published",
  };

  function goToPipelineStage(agentId, stage) {
    stopPolling();
    history.pushState({}, "", `/persona/${encodeURIComponent(agentId)}/${stage}`);
    renderPipelineStagePage(agentId, stage);
  }

  function goToDashboard(agentId) {
    history.pushState({}, "", `/persona/${encodeURIComponent(agentId)}`);
    renderDashboard(agentId);
  }

  function renderEventCard(ev) {
    const statusClass = `status-${ev.status}`;
    const titleNode = ev.sourceUrl
      ? el("a", { href: ev.sourceUrl, target: "_blank", rel: "noopener noreferrer", class: "headline" }, [ev.title || "Untitled"])
      : el("div", { class: "headline" }, [ev.title || "Untitled"]);

    const bodyBits = [];
    if (ev.snippet) bodyBits.push(el("p", { class: "event-snippet" }, [ev.snippet]));
    if (ev.reason) bodyBits.push(el("p", { class: "event-reason" }, [el("b", {}, ["Reason: "]), ev.reason]));
    if (ev.content) {
      const preview = ev.content.length > 400 ? ev.content.slice(0, 400) + "…" : ev.content;
      bodyBits.push(el("pre", { class: "event-content" }, [preview]));
    }

    return el("div", { class: "event-card" }, [
      el("div", { class: "event-card-top" }, [
        el("span", { class: `status-pill ${statusClass}` }, [STAGE_STATUS_LABEL[ev.status] || ev.status]),
        el("span", { class: "event-time" }, [fmtTime(ev.createdAt)]),
      ]),
      titleNode,
      ...bodyBits,
    ]);
  }

  async function renderPipelineStagePage(agentId, stage) {
    stopPolling();
    setCurrentAgentId(agentId);
    const stageDef = PIPELINE_STAGES.find((s) => s.key === stage) || PIPELINE_STAGES[0];
    APP.replaceChildren(loadingShell());

    let persona, stageData;
    try {
      [persona, stageData] = await Promise.all([
        apiGet(`/api/agent/persona?agentId=${encodeURIComponent(agentId)}`),
        apiGet(`/api/agent/pipeline/${stage}?agentId=${encodeURIComponent(agentId)}`),
      ]);
    } catch (err) {
      if (err.status === 404) {
        setCurrentAgentId(null);
        renderSetup(null, "That persona no longer exists. Set up a new one below.");
        return;
      }
      renderErrorShell(err.message, () => renderPipelineStagePage(agentId, stage));
      return;
    }

    const p = persona.persona || {};
    const events = stageData.events || [];

    // Group newest-first events by cycle, preserving the order cycles first appear in.
    const cycleOrder = [];
    const cycleMap = {};
    events.forEach((ev) => {
      const cid = ev.cycleId || "unknown";
      if (!cycleMap[cid]) {
        cycleMap[cid] = [];
        cycleOrder.push(cid);
      }
      cycleMap[cid].push(ev);
    });

    const topbar = el("div", { class: "topbar" }, [
      el("div", { class: "brand" }, [
        el("span", { class: "brand-mark", html: ICONS.flower }, []),
        "Greenhouse",
      ]),
      el("button", { class: "btn", onclick: () => goToDashboard(agentId) }, ["← Back to dashboard"]),
    ]);

    const header = el("div", { class: "stage-page-header" }, [
      el("span", { html: stageDef.icon, class: `stage-page-icon tone-${stageDef.tone}` }, []),
      el("div", {}, [
        el("div", { class: "stage-page-eyebrow" }, [p.name || "Untitled Persona"]),
        el("h1", {}, [stageDef.label]),
      ]),
    ]);

    const body = events.length === 0
      ? el("div", { class: "empty-state" }, [
          "No events logged yet for this stage. Once this persona runs a cycle, real discover/evaluate/write/publish activity will show up here.",
        ])
      : el("div", { class: "cycle-list" }, cycleOrder.map((cid) => {
          const group = cycleMap[cid];
          return el("div", { class: "cycle-group" }, [
            el("div", { class: "cycle-group-header" }, [
              el("span", {}, [cid === "unknown" ? "Unknown cycle" : `Cycle ${cid.slice(0, 8)}`]),
              el("span", {}, [fmtTime(group[0].createdAt)]),
              el("span", {}, [`${group.length} event${group.length === 1 ? "" : "s"}`]),
            ]),
            el("div", { class: "event-list" }, group.map(renderEventCard)),
          ]);
        }));

    APP.replaceChildren(topbar, el("div", { class: "stage-page" }, [header, body]));
  }

  // ---------- polling ----------

  function startPolling(agentId) {
    stopPolling();
    pollTimer = setInterval(async () => {
      try {
        const [persona, feed, rejected, memory, pipelineCounts] = await Promise.all([
          apiGet(`/api/agent/persona?agentId=${encodeURIComponent(agentId)}`),
          apiGet(`/api/agent/feed?agentId=${encodeURIComponent(agentId)}`),
          apiGet(`/api/agent/rejected?agentId=${encodeURIComponent(agentId)}`),
          apiGet(`/api/agent/memory?agentId=${encodeURIComponent(agentId)}`),
          apiGet(`/api/agent/pipeline/counts?agentId=${encodeURIComponent(agentId)}`),
        ]);
        const agentsList = await apiGet("/api/agent/list");
        paintDashboard(agentId, persona, feed, rejected, agentsList, memory, false, pipelineCounts);
      } catch (_) {
        // silent on background poll failure; next tick retries
      }
    }, POLL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // ---------- boot / routing ----------

  const STAGE_KEYS = new Set(PIPELINE_STAGES.map((s) => s.key));

  function parsePersonaPath(pathname) {
    const parts = pathname.split("/").filter(Boolean);
    if (parts[0] !== "persona" || !parts[1]) return null;
    const agentId = decodeURIComponent(parts[1]);
    const stage = parts[2] && STAGE_KEYS.has(parts[2]) ? parts[2] : null;
    return { agentId, stage };
  }

  async function routeFromLocation() {
    const parsed = parsePersonaPath(location.pathname);
    if (parsed) {
      if (parsed.stage) {
        await renderPipelineStagePage(parsed.agentId, parsed.stage);
      } else {
        await renderDashboard(parsed.agentId);
      }
      return;
    }
    const existing = getCurrentAgentId();
    if (existing) {
      await renderDashboard(existing);
    } else {
      renderSetup();
    }
  }

  window.addEventListener("popstate", () => {
    routeFromLocation();
  });

  (async function boot() {
    await routeFromLocation();
  })();
})();
