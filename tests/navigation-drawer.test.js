// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(
  path.join(__dirname, "..", "docs", "javascripts", "navigation-drawer.js"),
  "utf8",
);

class TestEvent {
  constructor(type, options = {}) {
    this.type = type;
    Object.assign(this, options);
    this.defaultPrevented = false;
  }

  preventDefault() {
    this.defaultPrevented = true;
  }
}

class TestElement {
  constructor(tagName, document) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = document;
    this.attributes = new Map();
    this.children = [];
    this.listeners = new Map();
    this.focusables = [];
    this.parentElement = null;
    this.hidden = false;
    this.inert = false;
    this.tabIndex = 0;
    this.visible = true;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type, listener) {
    this.listeners.set(
      type,
      (this.listeners.get(type) ?? []).filter((candidate) => candidate !== listener),
    );
  }

  dispatchEvent(event) {
    event.target ??= this;
    for (const listener of this.listeners.get(event.type) ?? []) listener(event);
  }

  append(...children) {
    for (const child of children) {
      child.parentElement = this;
      this.children.push(child);
    }
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  querySelectorAll() {
    return this.focusables;
  }

  contains(element) {
    for (let current = element; current; current = current.parentElement) {
      if (current === this) return true;
    }
    return false;
  }

  closest(selector) {
    for (let current = this; current; current = current.parentElement) {
      if (selector === "[inert]" && current.inert) return current;
      if (
        selector === "a[href]" &&
        current.tagName === "A" &&
        current.attributes.has("href")
      ) {
        return current;
      }
    }
    return null;
  }

  getClientRects() {
    return this.visible ? [{}] : [];
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }
}

class TestInput extends TestElement {
  constructor(document) {
    super("input", document);
    this.checked = false;
  }
}

class TestDocument extends TestElement {
  constructor() {
    super("document", null);
    this.ownerDocument = this;
    this.activeElement = null;
    this.readyState = "complete";
    this.elements = new Map();
    this.documentElement = new TestElement("html", this);
    this.documentElement.dataset = {};
    this.documentElement.classList = {
      add() {},
      remove() {},
    };
  }

  querySelector(selector) {
    return this.elements.get(selector) ?? null;
  }

  querySelectorAll() {
    return [];
  }
}

class TestMediaQuery extends TestElement {
  constructor(document, matches) {
    super("media-query", document);
    this.matches = matches;
  }
}

function createFixture({ modal = false, storedOpen = false } = {}) {
  const document = new TestDocument();
  const toggle = new TestInput(document);
  const sidebar = new TestElement("aside", document);
  const overlay = new TestElement("label", document);
  const button = new TestElement("label", document);
  const container = new TestElement("div", document);
  const header = new TestElement("header", document);
  const main = new TestElement("main", document);
  const firstLink = new TestElement("a", document);
  const hiddenLink = new TestElement("a", document);
  const lastLink = new TestElement("a", document);
  const outside = new TestElement("a", document);
  const media = new TestMediaQuery(document, modal);
  const storage = new Map([
    ["openshell.navigationDrawerOpen", String(storedOpen)],
  ]);

  firstLink.setAttribute("href", "/first/");
  hiddenLink.setAttribute("href", "/hidden/");
  hiddenLink.visible = false;
  lastLink.setAttribute("href", "/last/");
  sidebar.append(firstLink, hiddenLink, lastLink);
  sidebar.focusables = [firstLink, hiddenLink, lastLink];
  header.append(button);
  main.append(sidebar);

  document.elements.set("#__drawer", toggle);
  document.elements.set(".md-sidebar--primary", sidebar);
  document.elements.set('.md-overlay[for="__drawer"]', overlay);
  document.elements.set(".openshell-drawer-button", button);
  document.elements.set(".md-container", container);

  const window = {
    document$: undefined,
    getComputedStyle(element) {
      return { visibility: element.visible ? "visible" : "hidden" };
    },
    matchMedia() {
      return media;
    },
    requestAnimationFrame(callback) {
      callback();
      return 1;
    },
    sessionStorage: {
      getItem(key) {
        return storage.get(key) ?? null;
      },
      setItem(key, value) {
        storage.set(key, value);
      },
    },
  };

  vm.runInNewContext(script, {
    document,
    Element: TestElement,
    HTMLElement: TestElement,
    HTMLInputElement: TestInput,
    window,
  });

  return {
    button,
    document,
    firstLink,
    hiddenLink,
    lastLink,
    media,
    outside,
    sidebar,
    storage,
    toggle,
  };
}

test("desktop restores state without adding a duplicate navigation landmark", () => {
  const fixture = createFixture({ storedOpen: true });

  assert.equal(fixture.toggle.checked, true);
  assert.equal(fixture.document.documentElement.dataset.navigationDrawer, "open");
  assert.equal(fixture.sidebar.getAttribute("role"), null);
  assert.equal(fixture.button.getAttribute("aria-expanded"), "true");
});

test("mobile navigation closes the modal and clears saved state", () => {
  const fixture = createFixture({ modal: true, storedOpen: true });

  assert.equal(fixture.document.activeElement, fixture.firstLink);
  assert.equal(fixture.sidebar.getAttribute("role"), "dialog");
  assert.equal(fixture.sidebar.getAttribute("aria-modal"), "true");

  fixture.sidebar.dispatchEvent(
    new TestEvent("click", { target: fixture.lastLink }),
  );

  assert.equal(fixture.toggle.checked, false);
  assert.equal(fixture.storage.get("openshell.navigationDrawerOpen"), "false");
  assert.equal(fixture.sidebar.inert, true);
});

test("keyboard control, Escape, and visible focus endpoints work", () => {
  const fixture = createFixture({ modal: true });
  fixture.button.focus();

  fixture.document.dispatchEvent(new TestEvent("keydown", { key: "Enter" }));
  assert.equal(fixture.toggle.checked, false, "Zensical owns Enter activation");

  fixture.document.dispatchEvent(new TestEvent("keydown", { key: " " }));
  assert.equal(fixture.toggle.checked, true);

  fixture.firstLink.focus();
  fixture.document.dispatchEvent(
    new TestEvent("keydown", { key: "Tab", shiftKey: true }),
  );
  assert.equal(fixture.document.activeElement, fixture.lastLink);

  fixture.document.dispatchEvent(new TestEvent("keydown", { key: "Escape" }));
  assert.equal(fixture.toggle.checked, false);
  assert.equal(fixture.document.activeElement, fixture.button);
});

test("entering modal mode repairs focus", () => {
  const fixture = createFixture({ storedOpen: true });
  fixture.outside.focus();
  fixture.media.matches = true;

  fixture.media.dispatchEvent(new TestEvent("change"));

  assert.equal(fixture.sidebar.getAttribute("role"), "dialog");
  assert.equal(fixture.document.activeElement, fixture.firstLink);
});
