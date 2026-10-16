// ferryGL website: theme toggle, menus, install tabs, copy buttons, the docs outline and the
// run diagram.
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

  // The run diagram in docs/architecture.md. site/_runflow.html holds its model: the boxes,
  // arrows and texts, the steps of a run, and where everything goes in a wide and a narrow
  // layout. Hovering, focusing or tapping shows details; "Play a run" walks through the steps.
  var SVG_NS = "http://www.w3.org/2000/svg";
  var RUNFLOW_STEP_MS = 2000;
  var RUNFLOW_PACKET_MS = 800;

  function svgElement(name, attrs, parent) {
    var element = document.createElementNS(SVG_NS, name);
    Object.keys(attrs).forEach(function (key) {
      element.setAttribute(key, attrs[key]);
    });
    if (parent) parent.appendChild(element);
    return element;
  }

  // Model texts mark code with `backticks`.
  function richText(text) {
    var holder = document.createElement("div");
    holder.textContent = text;
    return holder.innerHTML.replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  // Where an arrow between two boxes starts and ends. It crosses the gap between them through
  // the middle of the span they share, so an arrow to the long Linear box lines up with the
  // step it comes from or goes to.
  function arrowEnds(a, b) {
    var left = Math.max(a[0], b[0]);
    var right = Math.min(a[0] + a[2], b[0] + b[2]);
    if (left < right) {
      var x = (left + right) / 2;
      return b[1] > a[1] ? [x, a[1] + a[3], x, b[1]] : [x, a[1], x, b[1] + b[3]];
    }
    var y = (Math.max(a[1], b[1]) + Math.min(a[1] + a[3], b[1] + b[3])) / 2;
    return b[0] > a[0] ? [a[0] + a[2], y, b[0], y] : [a[0], y, b[0] + b[2], y];
  }

  function setupRunFlows() {
    document.querySelectorAll(".runflow").forEach(function (figure) {
      var source = figure.querySelector(".runflow-model");
      var canvas = figure.querySelector(".runflow-canvas");
      if (!source || !canvas) return;
      try {
        setupRunFlow(figure, canvas, JSON.parse(source.textContent));
      } catch (e) {
        // A broken model leaves the figure empty rather than breaking the rest of the page.
      }
    });
  }

  function setupRunFlow(figure, canvas, model) {
    var panel = figure.querySelector(".runflow-panel");
    var playButton = figure.querySelector(".runflow-play");
    var playLabel = figure.querySelector(".runflow-play-label");
    var applySwitch = figure.querySelector(".runflow-switch input");
    var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    var nodes = {};
    var edges = {};
    var stepCount = 0;
    model.nodes.forEach(function (node) {
      nodes[node.id] = node;
      if (node.step) stepCount += 1;
    });
    model.edges.forEach(function (edge) {
      edge.key = edge.from + ">" + edge.to;
      edges[edge.key] = edge;
    });

    var drawn = { layout: null, svg: null, nodes: {}, edges: {} };
    var hover = null;
    var pinned = null;
    var playing = null; // during a replay: { steps, index, timer, target }
    var shownPanel = "";

    function applying() {
      return !applySwitch || applySwitch.checked;
    }

    function isOff(item) {
      return Boolean(item.apply) && !applying();
    }

    function draw() {
      var name = canvas.clientWidth >= 600 ? "wide" : "narrow";
      if (name === drawn.layout) return;
      var layout = model.layouts[name];
      canvas.textContent = "";
      canvas.classList.toggle("is-narrow", name === "narrow");
      var root = svgElement(
        "svg",
        {
          viewBox: "0 0 " + layout.width + " " + layout.height,
          role: "group",
          "aria-label": model.label,
        },
        canvas
      );
      drawn = { layout: name, svg: root, nodes: {}, edges: {} };
      var g = layout.group;
      svgElement(
        "rect",
        { class: "runflow-group", x: g[0], y: g[1], width: g[2], height: g[3], rx: 16 },
        root
      );
      var groupLabel = { class: "runflow-group-label", x: g[0] + 10, y: g[1] - 8 };
      svgElement("text", groupLabel, root).textContent = model.group;
      model.edges.forEach(function (edge) {
        drawEdge(root, edge, layout);
      });
      model.nodes.forEach(function (node) {
        drawNode(root, node, layout.nodes[node.id]);
      });
      root.addEventListener("click", function (event) {
        if (!event.target.closest(".runflow-node, .runflow-edge")) {
          pinned = null;
          refresh();
        }
      });
      refresh();
    }

    function drawEdge(root, edge, layout) {
      var ends = arrowEnds(layout.nodes[edge.from], layout.nodes[edge.to]);
      var dx = ends[2] - ends[0];
      var dy = ends[3] - ends[1];
      var length = Math.sqrt(dx * dx + dy * dy);
      var ux = dx / length;
      var uy = dy / length;
      var gap = 3;
      var x1 = ends[0] + ux * gap;
      var y1 = ends[1] + uy * gap;
      var tipX = ends[2] - ux * gap;
      var tipY = ends[3] - uy * gap;
      var baseX = tipX - ux * 8;
      var baseY = tipY - uy * 8;
      var kind = edge.kind || "step";
      var group = svgElement("g", { class: "runflow-edge runflow-edge--" + kind }, root);
      var line = { class: "runflow-edge-line", x1: x1, y1: y1, x2: baseX, y2: baseY };
      svgElement("line", line, group);
      svgElement(
        "path",
        {
          class: "runflow-edge-head",
          d:
            "M" + tipX + " " + tipY +
            "L" + (baseX - uy * 5) + " " + (baseY + ux * 5) +
            "L" + (baseX + uy * 5) + " " + (baseY - ux * 5) + "Z",
        },
        group
      );
      svgElement(
        "line",
        { class: "runflow-edge-hit", x1: ends[0], y1: ends[1], x2: ends[2], y2: ends[3] },
        group
      );
      if (edge.label && length >= 56) {
        var vertical = Math.abs(dx) < 1;
        svgElement(
          "text",
          vertical
            ? { class: "runflow-edge-label", x: x1 + 9, y: (y1 + tipY) / 2 + 4 }
            : {
                class: "runflow-edge-label",
                x: (x1 + tipX) / 2,
                y: y1 - 9,
                "text-anchor": "middle",
              },
          group
        ).textContent = edge.label;
      }
      drawn.edges[edge.key] = { element: group, ends: [x1, y1, tipX, tipY] };
      var target = { edge: edge.key };
      group.addEventListener("mouseenter", function () {
        setHover(target);
      });
      group.addEventListener("mouseleave", function () {
        setHover(null);
      });
      group.addEventListener("click", function () {
        togglePin(target);
      });
    }

    function drawNode(root, node, box) {
      var x = box[0];
      var y = box[1];
      var w = box[2];
      var h = box[3];
      var cx = x + w / 2;
      var cy = y + h / 2;
      var label = (node.step ? "Step " + node.step + ", " : "") + node.title + ": " + node.sub;
      var group = svgElement(
        "g",
        {
          class: "runflow-node runflow-node--" + (node.step ? "step" : "external"),
          tabindex: "0",
          role: "button",
          "aria-label": label,
        },
        root
      );
      svgElement("rect", { x: x, y: y, width: w, height: h, rx: 12 }, group);
      // The tall Linear box of the narrow layout reads bottom to top.
      var turn = box[4] === "vertical" ? { transform: "rotate(-90 " + cx + " " + cy + ")" } : {};
      var texts = svgElement("g", turn, group);
      var title = { class: "runflow-node-title", x: cx, y: cy - 2, "text-anchor": "middle" };
      svgElement("text", title, texts).textContent = node.title;
      var sub = { class: "runflow-node-sub", x: cx, y: cy + 15, "text-anchor": "middle" };
      svgElement("text", sub, texts).textContent = node.sub;
      if (node.step) {
        var badge = svgElement("g", { class: "runflow-badge", "aria-hidden": "true" }, group);
        svgElement("circle", { cx: x + 16, cy: y, r: 9 }, badge);
        var number = { x: x + 16, y: y + 3.5, "text-anchor": "middle" };
        svgElement("text", number, badge).textContent = node.step;
      }
      drawn.nodes[node.id] = group;
      var target = { node: node.id };
      group.addEventListener("mouseenter", function () {
        setHover(target);
      });
      group.addEventListener("mouseleave", function () {
        setHover(null);
      });
      group.addEventListener("focus", function () {
        setHover(target);
      });
      group.addEventListener("blur", function () {
        setHover(null);
      });
      group.addEventListener("click", function () {
        togglePin(target);
      });
      group.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          togglePin(target);
        }
      });
    }

    function same(a, b) {
      return Boolean(a && b) && a.node === b.node && a.edge === b.edge && a.step === b.step;
    }

    function setHover(target) {
      if (playing) return; // a replay owns the highlight until it ends or is stopped
      hover = target;
      refresh();
    }

    function togglePin(target) {
      stopPlaying();
      pinned = same(pinned, target) ? null : target;
      refresh();
    }

    // What is highlighted, and which boxes and arrows belong to it.
    function focusOf(target) {
      var related = { nodes: {}, edges: {} };
      var current = { nodes: {}, edges: {} };
      if (!target) return null;
      if (target.edge) {
        var edge = edges[target.edge];
        current.edges[edge.key] = related.edges[edge.key] = true;
        related.nodes[edge.from] = related.nodes[edge.to] = true;
      } else {
        var id = target.step ? target.step.node : target.node;
        current.nodes[id] = related.nodes[id] = true;
        var keys = target.step
          ? target.step.edges
          : model.edges
              .filter(function (edge) {
                return edge.from === id || edge.to === id;
              })
              .map(function (edge) {
                return edge.key;
              });
        keys.forEach(function (key) {
          related.edges[key] = true;
          if (target.step) current.edges[key] = true;
          related.nodes[edges[key].from] = related.nodes[edges[key].to] = true;
        });
      }
      return { related: related, current: current };
    }

    function refresh() {
      var target = playing ? playing.target : hover || pinned;
      var focus = focusOf(target);
      figure.classList.toggle("has-focus", Boolean(focus));
      Object.keys(drawn.nodes).forEach(function (id) {
        var element = drawn.nodes[id];
        element.classList.toggle("is-related", Boolean(focus && focus.related.nodes[id]));
        element.classList.toggle("is-current", Boolean(focus && focus.current.nodes[id]));
        element.classList.toggle("is-off", isOff(nodes[id]));
      });
      Object.keys(drawn.edges).forEach(function (key) {
        var element = drawn.edges[key].element;
        element.classList.toggle("is-related", Boolean(focus && focus.related.edges[key]));
        element.classList.toggle("is-current", Boolean(focus && focus.current.edges[key]));
        element.classList.toggle("is-off", isOff(edges[key]));
      });
      showPanel(describe(target));
    }

    function describe(target) {
      if (!target) return '<p class="runflow-hint">' + richText(model.intro) + "</p>";
      var kicker;
      var title;
      var text;
      var code = [];
      var io = null;
      var off = false;
      if (target.edge) {
        var edge = edges[target.edge];
        kicker = edge.kind === "read" ? "Read" : edge.kind === "write" ? "Write" : "Inside ferryGL";
        title = nodes[edge.from].title + " → " + nodes[edge.to].title;
        text = edge.text;
        off = isOff(edge);
      } else {
        var step = target.step;
        var node = nodes[step ? step.node : target.node];
        code = node.code;
        title = (step && step.title) || node.title;
        text = (step && step.text) || node.text;
        if (step && step.title) kicker = "Done";
        else kicker = node.step ? "Step " + node.step + " of " + stepCount : "Outside ferryGL";
        if (!(step && step.title) && (node.reads || node.writes)) io = node;
        off = !step && isOff(node);
      }
      var html = '<p class="runflow-kicker"><span>' + kicker + "</span>";
      code.forEach(function (name) {
        html += "<code>" + richText(name) + "</code>";
      });
      html += '</p><p class="runflow-title">' + richText(title) + "</p>";
      if (off) html += '<p class="runflow-off">Skipped without <code>--apply</code>.</p>';
      html += "<p>" + richText(text) + "</p>";
      if (io) {
        html += '<dl class="runflow-io">';
        if (io.reads) html += "<dt>Reads</dt><dd>" + richText(io.reads) + "</dd>";
        if (io.writes) html += "<dt>Writes</dt><dd>" + richText(io.writes) + "</dd>";
        html += "</dl>";
      }
      return html;
    }

    function showPanel(html) {
      if (html === shownPanel) return;
      shownPanel = html;
      panel.innerHTML = html;
      panel.classList.remove("is-fresh");
      void panel.offsetWidth; // restart the fade-in
      panel.classList.add("is-fresh");
    }

    function sendPacket(key) {
      var path = drawn.edges[key];
      if (!path || reduceMotion.matches) return;
      var ends = path.ends;
      var kind = edges[key].kind || "step";
      var dot = svgElement(
        "circle",
        { class: "runflow-packet runflow-packet--" + kind, r: 5, cx: ends[0], cy: ends[1] },
        drawn.svg
      );
      var start = null;
      var frame = function (now) {
        if (start === null) start = now;
        var t = Math.min((now - start) / RUNFLOW_PACKET_MS, 1);
        var eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
        dot.setAttribute("cx", ends[0] + (ends[2] - ends[0]) * eased);
        dot.setAttribute("cy", ends[1] + (ends[3] - ends[1]) * eased);
        if (t < 1) window.requestAnimationFrame(frame);
        else dot.remove();
      };
      window.requestAnimationFrame(frame);
    }

    function startPlaying() {
      var steps = model.steps.filter(function (step) {
        return !isOff(step);
      });
      if (!applying()) steps.push(model.dryRunEnd);
      hover = null;
      pinned = null;
      playing = { steps: steps, index: -1, timer: null, target: null };
      playButton.setAttribute("aria-pressed", "true");
      playLabel.textContent = "Stop";
      figure.classList.add("is-playing");
      nextStep();
    }

    function nextStep() {
      var index = playing.index + 1;
      if (index >= playing.steps.length) {
        // Leave the last step on screen, as if pinned, until the reader moves on.
        var last = playing.target;
        stopPlaying();
        pinned = last;
        refresh();
        return;
      }
      playing.index = index;
      playing.target = { step: playing.steps[index] };
      refresh();
      playing.steps[index].edges.forEach(sendPacket);
      playing.timer = window.setTimeout(nextStep, RUNFLOW_STEP_MS);
    }

    function stopPlaying() {
      if (!playing) return;
      window.clearTimeout(playing.timer);
      playing = null;
      playButton.setAttribute("aria-pressed", "false");
      playLabel.textContent = "Play a run";
      figure.classList.remove("is-playing");
    }

    playButton.addEventListener("click", function () {
      if (playing) {
        stopPlaying();
        refresh();
      } else {
        startPlaying();
      }
    });
    if (applySwitch) {
      applySwitch.addEventListener("change", function () {
        stopPlaying();
        refresh();
      });
    }
    figure.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        stopPlaying();
        pinned = null;
        refresh();
      }
    });
    if ("ResizeObserver" in window) {
      new ResizeObserver(draw).observe(canvas);
    } else {
      window.addEventListener("resize", draw);
    }
    draw();
  }

  setupTheme();
  setupToggle(".menu-toggle", "#site-nav");
  setupToggle(".docs-menu button", ".docs-sidebar");
  setupHeaderShadow();
  setupInstallTabs();
  setupCopyButtons();
  setupOutline();
  setupRunFlows();
})();
