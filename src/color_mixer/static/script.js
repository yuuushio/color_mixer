document.querySelectorAll("input").forEach((el) => {
  el.setAttribute("autocomplete", "off");
  el.setAttribute("spellcheck", "false");
  el.setAttribute("autocorrect", "off");
  el.setAttribute("autocapitalize", "off");
});

document.addEventListener("DOMContentLoaded", () => {
  const src = document.getElementById("ghostty-src");
  const out = document.getElementById("ghostty-css"); // now a DIV
  const btn = document.getElementById("ghostty-generate");
  const copyBtn = document.getElementById("ghostty-copy"); // optional

  if (!src || !out || !btn) return;

  const K_IN = "ghostty:src";
  const K_OUT = "ghostty:css:text"; // store pure text for easy copy

  // restore persisted input/output
  const sIn = sessionStorage.getItem(K_IN);
  const sOut = sessionStorage.getItem(K_OUT);
  if (sIn) src.value = sIn;
  if (sOut) renderTextIntoOutput(out, sOut); // render the saved text with swatches

  src.addEventListener("input", () => sessionStorage.setItem(K_IN, src.value));

  btn.addEventListener("click", () => {
    const vars = ghosttyToVars(src.value);
    const text = formatCssRootText(vars); // pure text
    renderTextIntoOutput(out, text); // HTML with swatches
    sessionStorage.setItem(K_OUT, text);
  });

  // Optional: one-click copy
  if (copyBtn) {
    copyBtn.addEventListener("click", async () => {
      const txt = sessionStorage.getItem(K_OUT) || collectOutputText(out);
      await navigator.clipboard.writeText(txt);
      copyBtn.textContent = "Copied!";
      setTimeout(() => (copyBtn.textContent = "Copy"), 1000);
    });
  }

  // ---- helpers ----
  function renderTextIntoOutput(container, text) {
    container.innerHTML = ""; // clear
    const pre = document.createElement("pre");
    const code = document.createElement("code");
    const HEX_LINE =
      /^\s*(--[a-z0-9-]+)\s*:\s*(#(?:[0-9a-f]{6}|[0-9a-f]{3}))(?![0-9a-f])\s*;?/i;

    // split into lines and decorate ANSI lines with a swatch
    const lines = text.split(/\r?\n/);
    for (const line of lines) {
      // match "  --ansi-12: #81a1c1;" etc.
      const m = line.match(HEX_LINE);
      const span = document.createElement("span");
      span.className = "decl";
      span.textContent = line;

      if (m) {
        span.classList.add("has-swatch");
        span.style.setProperty("--sw", m[2]); // the hex we captured
      }
      code.appendChild(span);
      // code.appendChild(document.createTextNode("\n"));
    }

    pre.appendChild(code);
    container.appendChild(pre);
  }

  function collectOutputText(container) {
    // get plain text without the pseudo-elements
    return container.innerText.replace(/\u200B/g, "");
  }

  function ghosttyToVars(text) {
    const kv = {};
    const palette = Array(16).fill(null);

    const lines = text.split(/\r?\n/);
    for (let raw of lines) {
      let line = raw.trim();
      if (
        !line ||
        line.startsWith(";") ||
        line.startsWith("#") ||
        line.startsWith("//")
      )
        continue;
      line = line.replace(/\s*(?:;|\/\/).*$/, "").trim();
      if (!line) continue;

      const m = line.match(/^([^=]+)=(.+)$/);
      if (!m) continue;
      const key = m[1].trim().toLowerCase();
      const val = m[2].trim();

      let pSingle = key.match(/^palette[\.\[]?(\d+)\]?$/);
      if (pSingle) {
        const idx = Number(pSingle[1]);
        if (idx >= 0 && idx < 16) palette[idx] = normColor(val);
        continue;
      }
      if (key === "palette") {
        const parts = val.split(/[\s,]+/).filter(Boolean);
        let anyPair = false;
        for (const t of parts) {
          const pm = t.match(/^(\d{1,2})\s*=\s*(.+)$/);
          if (pm) {
            const idx = Number(pm[1]);
            const c = normColor(pm[2]);
            if (idx >= 0 && idx < 16 && c) {
              palette[idx] = c;
              anyPair = true;
            }
          }
        }
        if (!anyPair) {
          const colors = parts.map(normColor).filter(Boolean);
          for (let i = 0; i < Math.min(colors.length, 16); i++)
            palette[i] = colors[i];
        }
        continue;
      }
      kv[key] = normColor(val) || val;
    }

    const out = new Map();
    const alias = [
      ["background", "--bg"],
      ["foreground", "--fg"],
      ["cursor-color", "--cursor"],
      ["cursor", "--cursor"],
      ["cursor-text", "--cursor-text"],
      ["selection-background", "--selection-bg"],
      ["selection-foreground", "--selection-fg"],
    ];
    for (const [k, v] of Object.entries(kv)) {
      const found = alias.find(([name]) => name === k);
      const varName = found
        ? found[1]
        : `--ghostty-${k.replace(/[^a-z0-9]+/g, "-")}`;
      out.set(varName, v);
    }
    palette.forEach((c, i) => c && out.set(`--ansi-${i}`, c));
    return out;
  }

  function formatCssRootText(varsMap) {
    const order = [
      "--bg",
      "--fg",
      "--cursor",
      "--selection-bg",
      "--selection-fg",
      ...Array.from({ length: 16 }, (_, i) => `--ansi-${i}`),
    ];
    const known = [],
      rest = [];
    for (const [k, v] of varsMap.entries())
      (order.includes(k) ? known : rest).push([k, v]);
    known.sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
    rest.sort((a, b) => a[0].localeCompare(b[0]));

    const lines = [
      ":root {",
      ...[...known, ...rest].map(([k, v]) => `  ${k}: ${v};`),
      "}",
    ];
    return lines.join("\n");
  }

  function normColor(token) {
    if (!token) return null;
    let t = token.trim();

    const rgb = t.match(
      /^rgba?\s*\(\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})/i,
    );
    if (rgb) {
      const r = clamp255(+rgb[1]),
        g = clamp255(+rgb[2]),
        b = clamp255(+rgb[3]);
      return `#${hex2(r)}${hex2(g)}${hex2(b)}`;
    }
    const hex = t.match(/^(0x[0-9a-f]{6}|#[0-9a-f]{3,8})$/i);
    if (hex) {
      let h = hex[1].toLowerCase();
      if (h.startsWith("0x")) h = `#${h.slice(2)}`;
      if (/^#[0-9a-f]{3}$/i.test(h)) {
        h =
          "#" +
          h
            .slice(1)
            .split("")
            .map((ch) => ch + ch)
            .join("");
      }
      return h.slice(0, 7);
    }
    return null;
  }
  const clamp255 = (x) => Math.max(0, Math.min(255, x | 0));
  const hex2 = (n) => n.toString(16).padStart(2, "0");
});

document.addEventListener("DOMContentLoaded", () => {
  const nf = document.querySelector(".number-field");
  if (!nf) return;
  const input = nf.querySelector("input[type=number]");
  const up = nf.querySelector(".step-increment-btn");
  const down = nf.querySelector(".step-decrement-btn");

  function clamp(val) {
    const min = input.min !== "" ? +input.min : -Infinity;
    const max = input.max !== "" ? +input.max : Infinity;
    return Math.min(max, Math.max(min, val));
  }
  function updateDisabled() {
    const v = +input.value;
    const min = input.min !== "" ? +input.min : -Infinity;
    const max = input.max !== "" ? +input.max : Infinity;
    up.disabled = v >= max;
    down.disabled = v <= min;
  }
  function step(dir) {
    // Use native stepUp/stepDown when possible
    try {
      dir > 0 ? input.stepUp() : input.stepDown();
    } catch {
      input.value = clamp((+input.value || 0) + dir * (+input.step || 1));
    }
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    updateDisabled();
  }

  up.addEventListener("click", () => step(+1));
  down.addEventListener("click", () => step(-1));
  input.addEventListener("input", updateDisabled);
  updateDisabled();
});
document.addEventListener("DOMContentLoaded", () => {
  // 1) Dropdown data & UI
  const ALGORITHMS = {
    srgb: "sRGB γ-encoded",
    linear: "Linear-light sRGB",
    oklab: "Oklab",
    okhsv: "OkHSV",
    cam16ucs: "CAM16-UCS",
    km_sub: "Kubelka–Munk",
  };
  let selected = "srgb";

  const dropdown = document.getElementById("algo-dropdown");
  const trigger = document.getElementById("algo-trigger");
  const menu = document.getElementById("algo-menu");

  function buildMenu() {
    menu.innerHTML = "";
    Object.entries(ALGORITHMS).forEach(([key, label]) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "algo-item";
      btn.textContent = label;
      btn.dataset.value = key;
      btn.addEventListener("click", () => selectAlgo(key));
      menu.appendChild(btn);
    });
  }

  function updateTrigger() {
    trigger.textContent = ALGORITHMS[selected];
    menu
      .querySelectorAll(".algo-item")
      .forEach((btn) =>
        btn.classList.toggle("active", btn.dataset.value === selected),
      );
  }

  function selectAlgo(key) {
    selected = key;
    dropdown.classList.remove("open");
    updateTrigger();
  }

  function renderChaos(palette) {
    const target = document.getElementById("chaos");
    if (!target || !Array.isArray(palette) || palette.length === 0) return;

    const first = palette[0];
    const last = palette[palette.length - 1];
    const mid = palette[Math.floor((palette.length - 1) / 2)]; // first of two middles

    // 45° linear gradient, explicit stops
    //`linear-gradient(135deg, ${first} 0%, ${mid} 50%, ${last} 100%)`;
    target.style.backgroundImage = `radial-gradient(circle, ${last} 0%, ${mid} 50%, ${first} 100%)`;
  }

  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    dropdown.classList.toggle("open");
  });
  document.addEventListener("click", () => dropdown.classList.remove("open"));

  const form = document.getElementById("mixform");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const a = document.getElementById("colA").value.replace(/^#/, "");
    const b = document.getElementById("colB").value.replace(/^#/, "");
    const n = +document.getElementById("steps").value;

    // use our dropdown choice here
    const resp = await fetch(
      `/mix?${new URLSearchParams({
        algo: selected,
        a,
        b,
        n,
      })}`,
    );
    const data = await resp.json();

    const container = document.getElementById("swatches");
    container.innerHTML = "";

    data.forEach((hex) => {
      const rgbVals = hex
        .slice(1)
        .match(/../g)
        .map((h) => parseInt(h, 16));

      const sw = document.createElement("div");
      sw.className = "swatch";
      const chip = document.createElement("div");
      chip.className = "color";
      chip.style.background = hex;

      const wrapHex = document.createElement("span");
      wrapHex.className = "value-wrapper";
      const hexEl = document.createElement("span");
      hexEl.className = "hex";
      hexEl.textContent = hex;
      wrapHex.appendChild(hexEl);

      const wrapRgb = document.createElement("span");
      wrapRgb.className = "value-wrapper";
      const rgbEl = document.createElement("span");
      rgbEl.className = "rgb";
      rgbEl.textContent = `(${rgbVals.join(",")})`;
      wrapRgb.appendChild(rgbEl);

      // copy-on-click + tick
      [hexEl, rgbEl].forEach((el) => {
        el.addEventListener("click", async (ev) => {
          ev.stopPropagation();
          const txt = el === hexEl ? hex : `rgb${el.textContent}`;
          await navigator.clipboard.writeText(txt);

          const wrapper = el.parentNode;
          const TICK_HOLD_MS = 1000; // how long to stay fully visible
          const tick = document.createElement("span");
          tick.className = "tick";
          tick.textContent = "✓";
          wrapper.appendChild(tick);

          // after hold, fade it out
          setTimeout(() => {
            tick.classList.add("fade");
          }, TICK_HOLD_MS);

          // remove once fade finishes
          tick.addEventListener("transitionend", () => tick.remove());
        });
      });

      sw.append(chip, wrapHex, wrapRgb);
      container.append(sw);
    });

    renderChaos(data);
  });

  // ---- Tabs: instant, stateful, ARIA-correct ----
  (function initTabs() {
    const TABS = [
      { btn: "#tab-btn-mix", panel: "#tab-mix" },
      { btn: "#tab-btn-blank", panel: "#tab-blank" },
    ];
    const $ = (s, r = document) => r.querySelector(s);

    const saved = sessionStorage.getItem("activeTab") || "tab-mix";
    activate(saved);

    TABS.forEach(({ btn }) => {
      $(btn).addEventListener("click", (e) => {
        e.preventDefault();
        activate(e.currentTarget.id.replace("tab-btn", "tab")); // no '#'
      });
      $(btn).addEventListener("keydown", (e) => {
        // Arrow key roving focus
        const idx = TABS.findIndex((t) => t.btn === `#${e.currentTarget.id}`);
        if (e.key === "ArrowRight" || e.key === "ArrowDown") {
          e.preventDefault();
          $(TABS[(idx + 1) % TABS.length].btn).focus();
        } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
          e.preventDefault();
          $(TABS[(idx - 1 + TABS.length) % TABS.length].btn).focus();
        } else if (e.key === "Home") {
          e.preventDefault();
          $(TABS[0].btn).focus();
        } else if (e.key === "End") {
          e.preventDefault();
          $(TABS[TABS.length - 1].btn).focus();
        } else if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          activate(`#${e.currentTarget.id}`.replace("tab-btn", "tab"));
        }
      });
    });

    function activate(panelId) {
      const id = panelId.replace(/^#/, ""); // normalize
      TABS.forEach(({ btn, panel }) => {
        const isActive = panel === `#${id}`;
        $(btn).classList.toggle("is-active", isActive);
        $(btn).setAttribute("aria-selected", String(isActive));
        $(panel).classList.toggle("is-active", isActive);
        // hard hide/show to avoid any accidental CSS overrides
        $(panel).toggleAttribute("hidden", !isActive);
      });
      sessionStorage.setItem("activeTab", panelId);
    }
  })();

  // Initialize everything
  buildMenu();
  updateTrigger();
  // auto-mix once on load
  setTimeout(() => form.dispatchEvent(new Event("submit")), 100);
});
