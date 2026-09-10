(() => {
  const drawerStateKey = "openshell.navigationDrawerOpen";
  const modalDrawerQuery = "(max-width: 63.99rem)";
  let cleanup = () => {};

  function enhanceNavigationDrawer() {
    cleanup();

    const toggle = document.querySelector("#__drawer");
    const sidebar = document.querySelector(".md-sidebar--primary");
    const overlay = document.querySelector('.md-overlay[for="__drawer"]');
    const modalDrawer = window.matchMedia(modalDrawerQuery);
    const button = document.querySelector(".openshell-drawer-button");

    if (
      !(toggle instanceof HTMLInputElement) ||
      !(sidebar instanceof HTMLElement) ||
      !(button instanceof HTMLElement)
    ) {
      cleanup = () => {};
      return;
    }

    sidebar.id = "primary-navigation";
    sidebar.setAttribute("aria-label", "Primary navigation");
    button.setAttribute("aria-controls", sidebar.id);

    let returnFocus = button;
    const backgroundElements = new Map();

    const rememberBackgroundElement = (element) => {
      if (element instanceof HTMLElement && element !== button && element !== sidebar) {
        backgroundElements.set(element, element.inert);
      }
    };

    Array.from(button.parentElement?.children ?? []).forEach(rememberBackgroundElement);
    Array.from(sidebar.parentElement?.children ?? []).forEach(rememberBackgroundElement);
    document
      .querySelectorAll('[data-md-component="skip"], [data-md-component="announce"]')
      .forEach(rememberBackgroundElement);

    const container = document.querySelector(".md-container");
    if (container instanceof HTMLElement) {
      Array.from(container.children)
        .filter((element) => !element.contains(sidebar))
        .forEach(rememberBackgroundElement);
    }

    const focusableElements = () =>
      Array.from(
        sidebar.querySelectorAll(
          'a[href], button:not([disabled]), input:not([disabled]):not(.md-toggle), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter(
        (element) =>
          element instanceof HTMLElement &&
          element.tabIndex >= 0 &&
          element.getClientRects().length > 0 &&
          window.getComputedStyle(element).visibility === "visible" &&
          !element.closest("[inert]"),
      );

    const synchronize = ({ moveFocus = false, restoreFocus = false } = {}) => {
      const isOpen = toggle.checked;
      const isModal = modalDrawer.matches;
      document.documentElement.dataset.navigationDrawer = isOpen ? "open" : "closed";
      button.setAttribute("aria-expanded", String(isOpen));
      button.setAttribute("aria-label", isOpen ? "Close navigation" : "Open navigation");
      sidebar.setAttribute("aria-hidden", String(!isOpen));
      if (isModal) {
        sidebar.setAttribute("role", "dialog");
        sidebar.setAttribute("aria-label", "Primary navigation");
      } else {
        sidebar.removeAttribute("role");
        sidebar.removeAttribute("aria-label");
      }
      if (isOpen && isModal) {
        sidebar.setAttribute("aria-modal", "true");
      } else {
        sidebar.removeAttribute("aria-modal");
      }
      sidebar.inert = !isOpen;
      backgroundElements.forEach((wasInert, element) => {
        element.inert = (isOpen && isModal) || wasInert;
      });

      if (isOpen && isModal && moveFocus) {
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
      writeDrawerState(isOpen);
      synchronize(options);
    };

    const onButtonClick = (event) => {
      event.preventDefault();
      setOpen(!toggle.checked, {
        moveFocus: !toggle.checked,
        restoreFocus: toggle.checked,
      });
    };

    const onToggleChange = () => {
      writeDrawerState(toggle.checked);
      synchronize();
    };
    const onSidebarClick = (event) => {
      const link = event.target instanceof Element && event.target.closest("a[href]");
      if (link && modalDrawer.matches) {
        setOpen(false);
      }
    };
    const onOverlayClick = (event) => {
      event.preventDefault();
      setOpen(false, { restoreFocus: true });
    };
    const onKeyDown = (event) => {
      if (
        document.activeElement === button &&
        (event.key === " " || event.key === "Enter")
      ) {
        event.preventDefault();
        onButtonClick(event);
        return;
      }

      if (!toggle.checked) return;

      if (event.key === "Escape") {
        event.preventDefault();
        setOpen(false, { restoreFocus: true });
        return;
      }

      if (event.key !== "Tab" || !modalDrawer.matches) return;

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
    sidebar.addEventListener("click", onSidebarClick);
    overlay?.addEventListener("click", onOverlayClick);
    const onDrawerModeChange = () => {
      const shouldMoveFocus =
        toggle.checked && modalDrawer.matches && !sidebar.contains(document.activeElement);
      synchronize({ moveFocus: shouldMoveFocus });
    };
    modalDrawer.addEventListener("change", onDrawerModeChange);
    document.addEventListener("keydown", onKeyDown);
    document.documentElement.classList.add("openshell-drawer-restoring");
    toggle.checked = readDrawerState();
    synchronize({ moveFocus: toggle.checked && modalDrawer.matches });
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        document.documentElement.classList.remove("openshell-drawer-restoring");
      });
    });

    cleanup = () => {
      button.removeEventListener("click", onButtonClick);
      toggle.removeEventListener("change", onToggleChange);
      sidebar.removeEventListener("click", onSidebarClick);
      overlay?.removeEventListener("click", onOverlayClick);
      modalDrawer.removeEventListener("change", onDrawerModeChange);
      document.removeEventListener("keydown", onKeyDown);
      document.documentElement.classList.remove("openshell-drawer-restoring");
      backgroundElements.forEach((wasInert, element) => {
        element.inert = wasInert;
      });
    };
  }

  const readDrawerState = () => {
    try {
      return window.sessionStorage.getItem(drawerStateKey) === "true";
    } catch {
      return false;
    }
  };

  const writeDrawerState = (isOpen) => {
    try {
      window.sessionStorage.setItem(drawerStateKey, String(isOpen));
    } catch {
      // Keep the drawer usable when browser storage is unavailable.
    }
  };

  if (window.document$?.subscribe) {
    window.document$.subscribe(enhanceNavigationDrawer);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", enhanceNavigationDrawer, { once: true });
  } else {
    enhanceNavigationDrawer();
  }
})();
