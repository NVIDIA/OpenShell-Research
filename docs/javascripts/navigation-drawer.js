(() => {
  let cleanup = () => {};

  function enhanceNavigationDrawer() {
    cleanup();

    const toggle = document.querySelector("#__drawer");
    const sidebar = document.querySelector(".md-sidebar--primary");
    const overlay = document.querySelector('.md-overlay[for="__drawer"]');
    const legacyControl = document.querySelector(
      '.md-header__button[for="__drawer"]',
    );

    if (!(toggle instanceof HTMLInputElement) || !(sidebar instanceof HTMLElement)) {
      cleanup = () => {};
      return;
    }

    let button = document.querySelector(".openshell-drawer-button");
    if (!(button instanceof HTMLButtonElement) && legacyControl instanceof HTMLElement) {
      button = document.createElement("button");
      button.type = "button";
      button.className = legacyControl.className;
      button.classList.add("openshell-drawer-button");
      button.innerHTML = legacyControl.innerHTML;
      legacyControl.replaceWith(button);
    }

    if (!(button instanceof HTMLButtonElement)) {
      cleanup = () => {};
      return;
    }

    sidebar.id = "primary-navigation";
    button.setAttribute("aria-controls", sidebar.id);

    let returnFocus = button;

    const focusableElements = () =>
      Array.from(
        sidebar.querySelectorAll(
          'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => element instanceof HTMLElement && !element.hidden);

    const synchronize = ({ moveFocus = false, restoreFocus = false } = {}) => {
      const isOpen = toggle.checked;
      button.setAttribute("aria-expanded", String(isOpen));
      button.setAttribute("aria-label", isOpen ? "Close navigation" : "Open navigation");
      sidebar.setAttribute("aria-hidden", String(!isOpen));
      sidebar.inert = !isOpen;

      if (isOpen && moveFocus) {
        focusableElements()[0]?.focus();
      } else if (!isOpen && restoreFocus) {
        returnFocus.focus();
      }
    };

    const setOpen = (isOpen, options = {}) => {
      if (isOpen) {
        returnFocus = button;
      }
      toggle.checked = isOpen;
      synchronize(options);
    };

    const onButtonClick = () => {
      setOpen(!toggle.checked, {
        moveFocus: !toggle.checked,
        restoreFocus: toggle.checked,
      });
    };

    const onToggleChange = () => synchronize();
    const onOverlayClick = (event) => {
      event.preventDefault();
      setOpen(false, { restoreFocus: true });
    };
    const onSidebarClick = (event) => {
      if (event.target instanceof Element && event.target.closest("a[href]")) {
        setOpen(false);
      }
    };
    const onKeyDown = (event) => {
      if (!toggle.checked) return;

      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false, { restoreFocus: true });
        return;
      }

      if (event.key !== "Tab") return;

      const focusable = focusableElements();
      if (!focusable.length) {
        event.preventDefault();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    button.addEventListener("click", onButtonClick);
    toggle.addEventListener("change", onToggleChange);
    overlay?.addEventListener("click", onOverlayClick);
    sidebar.addEventListener("click", onSidebarClick);
    document.addEventListener("keydown", onKeyDown);
    synchronize();

    cleanup = () => {
      button.removeEventListener("click", onButtonClick);
      toggle.removeEventListener("change", onToggleChange);
      overlay?.removeEventListener("click", onOverlayClick);
      sidebar.removeEventListener("click", onSidebarClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }

  if (window.document$?.subscribe) {
    window.document$.subscribe(enhanceNavigationDrawer);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", enhanceNavigationDrawer, { once: true });
  } else {
    enhanceNavigationDrawer();
  }
})();
