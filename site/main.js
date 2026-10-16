// ferryGL website: theme toggle, menus, install tabs, copy buttons and the docs outline.
(function () {
  "use strict";

  var root = document.documentElement;
  var THEME_KEY = "ferrygl-theme";

  function currentTheme() {
    if (root.dataset.theme) return root.dataset.theme;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  function setupTheme() {
    var button = document.querySelector(".theme-toggle");
    if (!button) return;
    button.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch (e) {
        // Storage can be unavailable, for example in a private window. The toggle still works.
      }
    });
  }

  function setupToggle(buttonSelector, panelSelector) {
    var button = document.querySelector(buttonSelector);
    var panel = document.querySelector(panelSelector);
    if (!button || !panel) return;
    button.addEventListener("click", function () {
      var open = panel.classList.toggle("is-open");
      button.setAttribute("aria-expanded", String(open));
    });
    panel.addEventListener("click", function (event) {
      if (event.target.closest("a")) {
        panel.classList.remove("is-open");
        button.setAttribute("aria-expanded", "false");
      }
    });
  }

  function setupHeaderShadow() {
    var header = document.querySelector(".site-header");
    if (!header) return;
    var update = function () {
      header.classList.toggle("is-scrolled", window.scrollY > 8);
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
  }

  function setupInstallTabs() {
    var tabs = document.querySelectorAll(".install-tabs [role=tab]");
    var command = document.getElementById("install-command");
    if (!tabs.length || !command) return;
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (other) {
          other.setAttribute("aria-selected", String(other === tab));
        });
        command.textContent = tab.dataset.command;
      });
    });
  }

  // Shell examples show a "$ " prompt and sometimes the output. Copy only the commands.
  function commandsOf(text) {
    var lines = text.replace(/\n+$/, "").split("\n");
    var commands = lines.filter(function (line) {
      return line.indexOf("$ ") === 0;
    });
    if (!commands.length) return lines.join("\n");
    return commands
      .map(function (line) {
        return line.slice(2);
      })
      .join("\n");
  }

  var COPY_ICONS =
    '<svg class="icon-copy" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>' +
    '<svg class="icon-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5"/></svg>';

  function copy(button, text) {
    var done = function () {
      button.classList.add("is-copied");
      button.setAttribute("aria-label", "Copied");
      setTimeout(function () {
        button.classList.remove("is-copied");
        button.setAttribute("aria-label", button.dataset.label || "Copy");
      }, 1600);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () {});
      return;
    }
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    try {
      document.execCommand("copy");
      done();
    } catch (e) {
      // Nothing more to try; the text is still selectable by hand.
    }
    document.body.removeChild(area);
  }

  function setupCopyButtons() {
    document.querySelectorAll("[data-copy-target]").forEach(function (button) {
      button.dataset.label = button.getAttribute("aria-label");
      button.addEventListener("click", function () {
        var target = document.getElementById(button.dataset.copyTarget);
        if (target) copy(button, target.textContent.trim());
      });
    });

    document.querySelectorAll(".code-block, .prose .highlight").forEach(function (block) {
      var pre = block.querySelector("pre");
      if (!pre) return;
      var button = document.createElement("button");
      button.type = "button";
      button.className = "copy-button";
      button.dataset.label = "Copy to clipboard";
      button.setAttribute("aria-label", button.dataset.label);
      button.innerHTML = COPY_ICONS;
      button.addEventListener("click", function () {
        copy(button, commandsOf(pre.textContent));
      });
      block.appendChild(button);
    });
  }

  // Highlight the section being read in the docs outline.
  function setupOutline() {
    var links = Array.prototype.slice.call(document.querySelectorAll(".docs-toc a"));
    if (!links.length || !("IntersectionObserver" in window)) return;
    var byId = {};
    var headings = [];
    links.forEach(function (link) {
      var id = decodeURIComponent(link.hash.slice(1));
      var heading = document.getElementById(id);
      if (heading) {
        byId[id] = link;
        headings.push(heading);
      }
    });
    var visible = {};
    var activate = function () {
      var current = null;
      for (var i = 0; i < headings.length; i++) {
        if (visible[headings[i].id]) {
          current = headings[i].id;
          break;
        }
      }
      if (!current) {
        // Between headings: keep the last one above the top of the window.
        for (var j = headings.length - 1; j >= 0; j--) {
          if (headings[j].getBoundingClientRect().top < 120) {
            current = headings[j].id;
            break;
          }
        }
      }
      links.forEach(function (link) {
        link.classList.toggle("is-active", byId[current] === link);
      });
    };
    var observer = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          visible[entry.target.id] = entry.isIntersecting;
        });
        activate();
      },
      { rootMargin: "-72px 0px -60% 0px" }
    );
    headings.forEach(function (heading) {
      observer.observe(heading);
    });
  }

  setupTheme();
  setupToggle(".menu-toggle", "#site-nav");
  setupToggle(".docs-menu button", ".docs-sidebar");
  setupHeaderShadow();
  setupInstallTabs();
  setupCopyButtons();
  setupOutline();
})();
