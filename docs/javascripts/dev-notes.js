// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

(() => {
  let activePage = null;
  let cleanup = () => {};
  const originalCards = new WeakMap();

  function enhanceDevNotes() {
    const page = document.querySelector(".dev-notes-page");
    if (page === activePage) return;
    cleanup();
    activePage = page;
    cleanup = () => {};
    if (!page) return;

    const form = page.querySelector(".dev-notes-filters");
    if (!form) return;
    const category = form.elements.namedItem("category");
    const author = form.elements.namedItem("author");
    const clear = form.querySelector(".dev-notes-filters__clear");
    const featured = page.querySelector(".dev-notes-featured");
    const recent = page.querySelector(".dev-notes-recent");
    const recentList = page.querySelector(".dev-notes-recent-list");
    const heading = page.querySelector("#featured-note-title");
    const recentHeading = page.querySelector("#recent-notes-title");
    const empty = page.querySelector(".dev-notes-filter-empty");
    if (!originalCards.has(page)) {
      originalCards.set(page, Array.from(page.querySelectorAll(".dev-note-card")));
    }
    const cards = originalCards.get(page).map((element) => ({
      element,
      category: element.dataset.category,
      authors: JSON.parse(element.dataset.authors),
    }));

    const applyFilters = () => {
      const url = new URL(window.location.href);
      let normalized = false;
      for (const select of [category, author]) {
        const value = url.searchParams.get(select.name) || "";
        const valid = Array.from(select.options).some((option) => option.value === value);
        select.value = valid ? value : "";
        if (!valid) {
          url.searchParams.delete(select.name);
          normalized = true;
        }
      }
      if (normalized) window.history.replaceState(window.history.state, "", url);

      const filtered = Boolean(category.value || author.value);
      const matches = cards.filter((card) =>
        (!category.value || card.category === category.value) &&
        (!author.value || card.authors.includes(author.value)),
      );
      const visible = new Set(matches);
      for (const card of cards) {
        card.element.hidden = !visible.has(card);
        card.element.classList.remove("dev-note-card--featured");
        card.element.classList.add("dev-note-card--recent");
      }
      matches.forEach((card, index) => {
        card.element.classList.toggle("dev-note-card--featured", index === 0);
        card.element.classList.toggle("dev-note-card--recent", index !== 0);
        card.element.querySelectorAll("img[data-featured-sizes]").forEach(image => {
          image.sizes = index === 0 ? image.dataset.featuredSizes : image.dataset.recentSizes;
          image.fetchPriority = index === 0 ? "high" : "auto";
        });
        if (index === 0) featured.insertBefore(card.element, empty);
        else recentList.append(card.element);
      });
      if (recent) recent.hidden = matches.length < 2;
      if (recentHeading) recentHeading.textContent = filtered ? "More notes" : "Recent notes";
      heading.textContent = filtered
        ? `${matches.length} ${matches.length === 1 ? "note" : "notes"}`
        : "Featured note";
      clear.hidden = !filtered;
      empty.hidden = matches.length !== 0;
      const categoryName = category.selectedOptions[0].textContent;
      const authorName = author.selectedOptions[0].textContent;
      empty.textContent = category.value && author.value
        ? `No ${categoryName} by ${authorName} yet.`
        : category.value
          ? `No ${categoryName} yet.`
          : `No notes by ${authorName} yet.`;
    };

    const updateUrl = (event) => {
      event.preventDefault();
      const url = new URL(window.location.href);
      for (const select of [category, author]) {
        if (select.value) url.searchParams.set(select.name, select.value);
        else url.searchParams.delete(select.name);
      }
      if (url.href !== window.location.href) {
        window.history.pushState(window.history.state, "", url);
      }
      applyFilters();
    };
    const clearFilters = (event) => {
      category.value = "";
      author.value = "";
      category.focus();
      updateUrl(event);
    };

    form.addEventListener("change", updateUrl);
    form.addEventListener("submit", updateUrl);
    clear.addEventListener("click", clearFilters);
    window.addEventListener("popstate", applyFilters);
    applyFilters();
    form.hidden = false;
    cleanup = () => {
      form.removeEventListener("change", updateUrl);
      form.removeEventListener("submit", updateUrl);
      clear.removeEventListener("click", clearFilters);
      window.removeEventListener("popstate", applyFilters);
    };
  }

  if (typeof document$ !== "undefined") {
    document$.subscribe(enhanceDevNotes);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", enhanceDevNotes);
  } else {
    enhanceDevNotes();
  }
})();
