/* SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved. */
/* SPDX-License-Identifier: Apache-2.0 */
(() => {
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const button = (text, action) => {
    const node = element("button", "pi-traces__button", text);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  };
  function messageCard(message, run) {
    const isTool = ["call", "result"].includes(message.role);
    const card = element("article", `pi-chat pi-chat--${message.role}${message.error ? " pi-chat--error" : ""}`);
    card.dataset.line = message.line;
    card.dataset.block = message.block;
    const labels = { user: "User", assistant: run.label, call: "Tool call", result: "Tool result" };
    const meta = element(isTool ? "div" : "summary", "pi-chat__meta");
    meta.append(element("span", "pi-chat__avatar", { user: "U", assistant: "Assistant", call: ">_", result: "↳" }[message.role]));
    meta.append(element("strong", "", labels[message.role]));
    if (isTool) meta.append(element("span", "pi-chat__tool", message.tool));
    meta.append(element("span", "pi-chat__source", `JSONL ${message.line}`));
    if (isTool) card.append(meta);

    const body = element("div", "pi-chat__body");
    if (isTool) {
      const disclosure = element("details", "pi-chat__disclosure");
      disclosure.dataset.messageGroup = "tools";
      const summary = element("summary");
      const lines = message.text.split("\n").length - Number(message.text.endsWith("\n"));
      const label = message.role === "call"
        ? `${message.language === "bash" ? "Shell command" : "JSON arguments"}`
        : `${message.error ? "Error output" : "Output"} · ${lines} ${lines === 1 ? "line" : "lines"} · saved output`;
      summary.append(element("span", "", label));
      summary.append(element("span", "pi-chat__disclosure-action"));
      const pre = element("pre", "pi-chat__code");
      const code = element("code");
      // Only the generator's escaped output / sanitized Markdown reaches innerHTML.
      code.innerHTML = message.html;
      pre.append(code);
      disclosure.append(summary, pre);
      if (message.role === "call" && message.language === "bash") {
        const extra = Object.fromEntries(Object.entries(message.arguments).filter(([key]) => key !== "command"));
        if (Object.keys(extra).length) {
          disclosure.append(element("pre", "pi-chat__arguments", JSON.stringify(extra, null, 2)));
        }
      }
      body.append(disclosure);
    } else {
      body.innerHTML = message.html;
    }
    if (isTool) {
      card.append(body);
    } else {
      const disclosure = element("details", "pi-chat__message");
      disclosure.dataset.messageGroup = message.role;
      disclosure.open = true;
      meta.append(element("span", "pi-chat__disclosure-action"));
      disclosure.append(meta, body);
      card.append(disclosure);
    }
    return card;
  }

  async function initialize(viewer) {
    if (viewer.dataset.initialized) return;
    viewer.dataset.initialized = "true";
    const loading = viewer.querySelector(".pi-traces__loading");
    const app = viewer.querySelector(".pi-traces__app");
    const content = viewer.querySelector(".pi-traces__content");
    const collapse = viewer.querySelector(".pi-traces__collapse");
    const setExpanded = (expanded) => {
      content.hidden = !expanded;
      collapse.setAttribute("aria-expanded", String(expanded));
      collapse.textContent = expanded ? "Collapse transcripts −" : "Expand transcripts +";
    };
    collapse.addEventListener("click", () => setExpanded(content.hidden));
    collapse.hidden = false;
    try {
      // Markdown links are rewritten for the site's project / PR-preview prefix.
      const source = new URL(viewer.querySelector(".pi-traces__data").href);
      const response = await fetch(source);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const { runs } = await response.json();
      const modelRuns = runs.filter(run => run.admission === "off");
      const tabs = element("div", "pi-traces__tabs");
      tabs.setAttribute("role", "tablist");
      tabs.setAttribute("aria-label", "Choose a model");
      const modelButtons = [];
      const groups = [];
      let admission = "off";

      const selectMode = (group, mode, focus = false) => {
        admission = mode;
        group.buttons.forEach((tab, index) => {
          const active = tab.dataset.admission === mode;
          tab.setAttribute("aria-selected", String(active));
          tab.tabIndex = active ? 0 : -1;
          group.panels[index].hidden = !active;
          if (active && focus) tab.focus();
        });
      };
      const selectModel = (index, focus = false) => {
        modelButtons.forEach((tab, i) => {
          tab.setAttribute("aria-selected", String(index === i));
          tab.tabIndex = index === i ? 0 : -1;
          groups[i].container.hidden = index !== i;
        });
        selectMode(groups[index], admission);
        if (focus) modelButtons[index].focus();
      };
      const keyboardTabs = (list, buttons, select) => {
        list.addEventListener("keydown", event => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const current = buttons.indexOf(document.activeElement);
          const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1
            : (current + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
          select(next, true);
        });
      };
      modelRuns.forEach((run, index) => {
        const key = run.id.slice(0, -4);
        const tab = button("", () => selectModel(index));
        tab.className = "pi-traces__tab";
        tab.id = `trace-model-tab-${key}`;
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-controls", `trace-model-panel-${key}`);
        tab.append(element("strong", "", run.label));
        tabs.append(tab);
        modelButtons.push(tab);
        const container = element("section", "pi-traces__model-panel");
        container.id = `trace-model-panel-${key}`;
        container.setAttribute("role", "tabpanel");
        container.setAttribute("aria-labelledby", tab.id);
        const modes = element("div", "pi-traces__modes");
        modes.setAttribute("role", "tablist");
        modes.setAttribute("aria-label", `${run.label} redaction method`);
        groups.push({ key, container, modes, buttons: [], panels: [] });
      });
      runs.forEach(run => {
        // End the displayed excerpt at disclosure, even if the model doubts it.
        // Keep the generated source data and the complete assistant reply intact.
        const disclosure = run.admission === "off" && run.messages.find(message =>
          message.role === "assistant" && message.text.includes("csagan34@palebluedot.edu"));
        const messages = disclosure
          ? run.messages.filter(message => message.line <= disclosure.line)
          : run.messages;
        const promptCount = Math.max(...messages.map(message => message.prompt));
        const responseCount = new Set(messages
          .filter(message => ["assistant", "call"].includes(message.role))
          .map(message => message.line)).size;
        const group = groups.find(item => run.id.startsWith(`${item.key}-`));
        const redactionLabel = run.admission === "on" ? "Admission redacted" : "Network redacted";
        const tab = button("", () => selectMode(group, run.admission));
        tab.className = "pi-traces__mode-tab";
        tab.id = `trace-tab-${run.id}`;
        tab.dataset.admission = run.admission;
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-controls", `trace-panel-${run.id}`);
        tab.append(element("strong", "", redactionLabel));
        tab.append(element("span", "", run.admission === "on" ? "Redacted before storage" : "Redacted after storage"));
        group.modes.append(tab);
        group.buttons.push(tab);

        const panel = element("section", "pi-traces__panel");
        panel.id = `trace-panel-${run.id}`;
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", tab.id);
        panel.tabIndex = 0;
        const header = element("div", "pi-traces__run");
        const heading = element("div");
        heading.append(element("strong", "pi-traces__run-name", run.label));
        heading.append(element("span", "pi-traces__run-settings", `Thinking: ${run.thinking} · ${promptCount} prompts · ${responseCount} assistant responses`));
        const outcome = element("span", `pi-traces__outcome pi-traces__outcome--${run.admission}`, run.admission === "off" ? "Email leaked" : "Email protected");
        header.append(heading, outcome);

        const toolbar = element("div", "pi-traces__toolbar");
        const jumps = element("div", "pi-traces__jumps");
        const scroll = element("div", "pi-traces__conversation");
        scroll.id = `trace-conversation-${run.id}`;
        scroll.tabIndex = 0;
        scroll.setAttribute("role", "region");
        scroll.setAttribute("aria-label", `${run.label}, ${redactionLabel.toLowerCase()}, ${disclosure ? "conversation through first email disclosure" : "full visible conversation"}`);
        const jump = (target) => {
          const node = target === "start" ? scroll.firstElementChild
            : scroll.querySelector(".pi-chat:last-child");
          if (!node) return;
          if (target === "end") {
            const disclosure = node.querySelector(".pi-chat__message");
            if (disclosure) disclosure.open = true;
          }
          scroll.scrollTop += node.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 20;
          scroll.focus({ preventScroll: true });
        };
        jumps.append(button("Beginning", () => jump("start")), button("Final answer ↓", () => jump("end")));
        const display = element("details", "pi-traces__display");
        const displayToggle = element("summary", "", "Display");
        const chevron = element("span", "pi-traces__display-chevron", "⌄");
        chevron.setAttribute("aria-hidden", "true");
        displayToggle.append(chevron);
        const expand = element("div", "pi-traces__expand");
        expand.id = `trace-display-options-${run.id}`;
        expand.setAttribute("role", "group");
        expand.setAttribute("aria-label", "Expand or collapse messages");
        displayToggle.setAttribute("aria-controls", expand.id);
        display.append(displayToggle, expand);
        display.addEventListener("keydown", event => {
          if (event.key !== "Escape") return;
          event.preventDefault();
          event.stopPropagation();
          display.open = false;
          displayToggle.focus();
        });
        display.addEventListener("focusout", event => {
          if (!display.contains(event.relatedTarget)) display.open = false;
        });
        toolbar.append(jumps, display);
        let prompt;
        messages.forEach(message => {
          if (message.prompt !== prompt) {
            prompt = message.prompt;
            scroll.append(element("div", "pi-traces__prompt", `Prompt ${String(prompt).padStart(2, "0")} / ${String(promptCount).padStart(2, "0")}`));
          }
          scroll.append(messageCard(message, run));
        });
        const footer = element("div", "pi-traces__footer");
        footer.append(element("span", "", `${disclosure ? "Through first email disclosure" : "Complete visible trace"} · source line on every message`));
        panel.append(header, toolbar, scroll, footer);
        [["user", "user messages"], ["assistant", "assistant messages"], ["tools", "tools"]].forEach(([role, label]) => {
          const disclosures = [...scroll.querySelectorAll(`details[data-message-group="${role}"]`)];
          const allOpen = () => disclosures.every(detail => detail.open);
          const control = button("", () => {
            const open = !allOpen();
            disclosures.forEach(detail => { detail.open = open; });
            sync();
          });
          control.dataset.messageGroup = role;
          control.setAttribute("aria-controls", scroll.id);
          control.disabled = disclosures.length === 0;
          const sync = () => {
            const action = disclosures.length && allOpen() ? "Collapse" : "Expand";
            control.textContent = `${action} all`;
            control.setAttribute("aria-label", `${action} all ${label}`);
          };
          // A partial selection offers Expand all; controls never have a mixed state.
          disclosures.forEach(detail => detail.addEventListener("toggle", sync));
          sync();
          const row = element("div", "pi-traces__display-row");
          row.append(element("span", "", label[0].toUpperCase() + label.slice(1)), control);
          expand.append(row);
        });
        group.panels.push(panel);
      });
      keyboardTabs(tabs, modelButtons, selectModel);
      const legend = element("div", "pi-traces__legend");
      [["user", "User"], ["assistant", "Assistant"], ["call", "Tool call"], ["result", "Tool result"]].forEach(([role, label]) => {
        legend.append(element("span", `pi-traces__key pi-traces__key--${role}`, label));
      });
      groups.forEach(group => {
        group.container.append(group.modes, legend.cloneNode(true), ...group.panels);
        keyboardTabs(group.modes, group.buttons, (index, focus) => selectMode(group, group.buttons[index].dataset.admission, focus));
        selectMode(group, "off");
      });
      app.append(tabs, ...groups.map(group => group.container));
      selectModel(groups.findIndex(group => group.key === "kimi"));
      app.hidden = false;
      loading.remove();
      const followSourceLink = () => {
        if (!viewer.isConnected) {
          window.removeEventListener("hashchange", followSourceLink);
          document.removeEventListener("click", followRepeatedSourceLink);
          return;
        }
        const match = /^#trace-source-(kimi|glm|opus48|opus55)-(on|off)$/.exec(window.location.hash);
        if (!match) return;
        setExpanded(true);
        admission = match[2];
        selectModel(groups.findIndex(group => group.key === match[1]));
        document.getElementById(`trace-panel-${match[1]}-${match[2]}`).scrollIntoView({ block: "start" });
      };
      const followRepeatedSourceLink = (event) => {
        viewer.querySelectorAll(".pi-traces__display[open]").forEach(display => {
          if (!display.contains(event.target)) display.open = false;
        });
        const link = event.target.closest("a[href]");
        if (link?.href === window.location.href) followSourceLink();
      };
      window.addEventListener("hashchange", followSourceLink);
      document.addEventListener("click", followRepeatedSourceLink);
      followSourceLink();
    } catch (error) {
      loading.textContent = "The transcript viewer could not load. Refresh the page to try again.";
      console.error("Pi transcript viewer:", error);
    }
  }

  function enhance() {
    document.querySelectorAll(".pi-traces").forEach(initialize);
  }
  if (window.document$?.subscribe) window.document$.subscribe(enhance);
  else if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", enhance, { once: true });
  else enhance();
})();
