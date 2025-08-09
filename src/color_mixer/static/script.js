(() => {
  "use strict";

  /* ---------------------------- tiny DOM helpers ---------------------------- */
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  // normalize all inputs once (your original flags)
  $$("input").forEach((el) => {
    el.setAttribute("autocomplete", "off");
    el.setAttribute("spellcheck", "false");
    el.setAttribute("autocorrect", "off");
    el.setAttribute("autocapitalize", "off");
  });

  /* -------------------------------- Dropdown ------------------------------- */
  // Reusable, zero-dependency. Expects: root contains a trigger and a menu.
  // Works with either IDs or classes you already use.
  class Dropdown {
    /**
     * @param {string|Element} root
     * @param {Object} opts
     * @param {Object|Array} opts.items  map {value: label} or [{value,label}]
     * @param {string} [opts.value]      initial value
     * @param {string} [opts.itemClass]  class on each item button
     * @param {string} [opts.openClass]  class toggled on root when open
     * @param {Function} [opts.onSelect] callback(value)
     * @param {string} [opts.triggerSel] selector inside root for trigger
     * @param {string} [opts.menuSel]    selector inside root for menu
     */
    constructor(root, opts = {}) {
      this.root = typeof root === "string" ? $(root) : root;
      if (!this.root) return;

      const {
        items = {},
        value = null,
        itemClass = "algo-item",
        openClass = "open",
        onSelect = null,
        triggerSel = "#algo-trigger, .trigger, [data-trigger]",
        menuSel = "#algo-menu, .menu, [data-menu]",
      } = opts;

      this.itemClass = itemClass;
      this.openClass = openClass;
      this.onSelect = onSelect;

      this.trigger = $(triggerSel, this.root);
      this.menu = $(menuSel, this.root);

      this._items = this._normalizeItems(items);
      this._value = null;

      if (!this.trigger || !this.menu) return;

      this._buildMenu();
      this.set(value ?? this._items[0]?.value ?? null, { silent: true });

      // open/close
      this._onDocClick = (e) => {
        if (!this.root.contains(e.target)) this.close();
      };
      this.trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        this.toggle();
      });
    }

    _normalizeItems(items) {
      if (Array.isArray(items))
        return items.map(({ value, label }) => ({ value, label }));
      return Object.entries(items).map(([value, label]) => ({ value, label }));
    }

    _buildMenu() {
      this.menu.innerHTML = "";
      this._items.forEach(({ value, label }) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = this.itemClass;
        btn.dataset.value = value;
        btn.textContent = label;
        btn.addEventListener("click", () => this.set(value));
        this.menu.appendChild(btn);
      });
      this._buttons = $$(`.${this.itemClass}`, this.menu);
    }

    open() {
      this.root.classList.add(this.openClass);
      document.addEventListener("click", this._onDocClick);
    }
    close() {
      this.root.classList.remove(this.openClass);
      document.removeEventListener("click", this._onDocClick);
    }
    toggle() {
      this.root.classList.contains(this.openClass) ? this.close() : this.open();
    }

    set(value, { silent = false } = {}) {
      if (value == null || value === this._value) return;
      this._value = value;
      if (this.trigger)
        this.trigger.textContent = this._labelFor(value) ?? String(value);
      if (this._buttons) {
        this._buttons.forEach((b) => {
          const is = b.dataset.value === value;
          b.classList.toggle("active", is);
          b.setAttribute("aria-selected", is ? "true" : "false");
        });
      }
      if (!silent) {
        this.close();
        this.root.dispatchEvent(
          new CustomEvent("change", { detail: { value } }),
        );
        if (this.onSelect) this.onSelect(value);
      }
    }
    get value() {
      return this._value;
    }
    _labelFor(v) {
      return this._items.find((i) => i.value === v)?.label ?? null;
    }
  }

  /* ------------------------------- NumberField ------------------------------ */
  class NumberField {
    constructor(rootSel = ".number-field") {
      this.root = $(rootSel);
      if (!this.root) return;

      this.input = this.root.querySelector("input[type=number]");
      this.up = this.root.querySelector(".step-increment-btn");
      this.down = this.root.querySelector(".step-decrement-btn");
      if (!this.input || !this.up || !this.down) return;

      this.up.addEventListener("click", () => this.step(+1));
      this.down.addEventListener("click", () => this.step(-1));
      this.input.addEventListener("input", () => this._updateDisabled());
      this._updateDisabled();
    }
    _clamp(v) {
      const min = this.input.min !== "" ? +this.input.min : -Infinity;
      const max = this.input.max !== "" ? +this.input.max : +Infinity;
      return Math.min(max, Math.max(min, v));
    }
    step(dir) {
      try {
        dir > 0 ? this.input.stepUp() : this.input.stepDown();
      } catch {
        this.input.value = this._clamp(
          (+this.input.value || 0) + dir * (+this.input.step || 1),
        );
      }
      this.input.dispatchEvent(new Event("input", { bubbles: true }));
      this.input.dispatchEvent(new Event("change", { bubbles: true }));
      this._updateDisabled();
    }
    _updateDisabled() {
      const v = +this.input.value;
      const min = this.input.min !== "" ? +this.input.min : -Infinity;
      const max = this.input.max !== "" ? +this.input.max : +Infinity;
      this.up.disabled = v >= max;
      this.down.disabled = v <= min;
    }
  }

  /* ---------------------------------- Tabs --------------------------------- */
  class Tabs {
    /**
     * @param {{btn:string,panel:string}[]} config
     * @param {{storageKey?:string, indicator?:string}} opts
     */
    constructor(config, opts = {}) {
      this.config = config;
      this.key = opts.storageKey ?? "activeTab";
      this.indicator = opts.indicator ? $(opts.indicator) : null;

      if (!config?.length) return;

      const saved =
        sessionStorage.getItem(this.key) || config[0].panel.replace("#", "");
      this.activate(saved, { immediate: true });

      config.forEach(({ btn }) => {
        const el = $(btn);
        if (!el) return;
        el.addEventListener("click", (e) => {
          e.preventDefault();
          this.activate(e.currentTarget.id.replace("tab-btn", "tab"));
        });
      });

      // align indicator on first paint & resize
      requestAnimationFrame(() => this._moveIndicatorTo($(".tab.is-active")));
      let rAF;
      window.addEventListener("resize", () => {
        cancelAnimationFrame(rAF);
        rAF = requestAnimationFrame(() =>
          this._moveIndicatorTo($(".tab.is-active")),
        );
      });
    }

    activate(panelId, { immediate = false } = {}) {
      const id = panelId.replace(/^#/, "");
      this.config.forEach(({ btn, panel }) => {
        const isActive = panel === `#${id}`;
        $(btn)?.classList.toggle("is-active", isActive);
        $(btn)?.setAttribute("aria-selected", String(isActive));
        const p = $(panel);
        if (p) {
          p.classList.toggle("is-active", isActive);
          p.toggleAttribute("hidden", !isActive);
        }
        if (isActive) {
          if (this.indicator && immediate)
            this.indicator.style.transition = "none";
          requestAnimationFrame(() => {
            this._moveIndicatorTo($(btn));
            if (this.indicator && immediate) {
              requestAnimationFrame(() => {
                this.indicator.style.transition = "";
              });
            }
          });
        }
      });
      sessionStorage.setItem(this.key, id);
    }

    _moveIndicatorTo(btn) {
      if (!this.indicator || !btn) return;
      const x = btn.offsetLeft;
      const w = btn.offsetWidth;
      this.indicator.style.transform = `translateX(${x}px)`;
      this.indicator.style.width = `${w}px`;
      this.indicator.style.opacity = "1";
    }
  }

  /* ----------------------------- Ghostty helpers ---------------------------- */
  const Ghostty = {
    toVars(text) {
      const kv = {};
      const palette = Array(16).fill(null);
      const lines = text.split(/\r?\n/);

      const normColor = (token) => {
        if (!token) return null;
        let t = token.trim();
        const rgb = t.match(
          /^rgba?\s*\(\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})\s*,\s*([0-9]{1,3})/i,
        );
        if (rgb) {
          const clamp = (x) => Math.max(0, Math.min(255, x | 0));
          const hex2 = (n) => n.toString(16).padStart(2, "0");
          const r = clamp(+rgb[1]),
            g = clamp(+rgb[2]),
            b = clamp(+rgb[3]);
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
      };

      for (let raw of lines) {
        let line = raw.trim();
        if (!line || /^([;#]|\/\/)/.test(line)) continue;
        line = line.replace(/\s*(?:;|\/\/).*$/, "").trim();
        if (!line) continue;

        const m = line.match(/^([^=]+)=(.+)$/);
        if (!m) continue;
        const key = m[1].trim().toLowerCase();
        const val = m[2].trim();

        const pSingle = key.match(/^palette[\.\[]?(\d+)\]?$/);
        if (pSingle) {
          const idx = +pSingle[1];
          if (idx >= 0 && idx < 16) palette[idx] = normColor(val);
          continue;
        }
        if (key === "palette") {
          const parts = val.split(/[\s,]+/).filter(Boolean);
          let anyPair = false;
          for (const t of parts) {
            const pm = t.match(/^(\d{1,2})\s*=\s*(.+)$/);
            if (pm) {
              const idx = +pm[1];
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
    },

    formatRoot(varsMap) {
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

      return [
        ":root {",
        ...[...known, ...rest].map(([k, v]) => `  ${k}: ${v};`),
        "}",
      ].join("\n");
    },

    renderOutput(container, text) {
      container.innerHTML = "";
      const pre = document.createElement("pre");
      const code = document.createElement("code");
      const HEX_LINE =
        /^\s*(--[a-z0-9-]+)\s*:\s*(#(?:[0-9a-f]{6}|[0-9a-f]{3}))(?![0-9a-f])\s*;?/i;

      text.split(/\r?\n/).forEach((line) => {
        const m = line.match(HEX_LINE);
        const span = document.createElement("span");
        span.className = "decl";
        span.textContent = line;
        if (m) {
          span.classList.add("has-swatch");
          span.style.setProperty("--sw", m[2]);
        }
        code.appendChild(span);
      });

      pre.appendChild(code);
      container.appendChild(pre);
    },

    collectText(container) {
      return container.innerText.replace(/\u200B/g, "");
    },
  };

  async function copyText(txt, btn) {
    try {
      await navigator.clipboard.writeText(txt);
      flash(btn, "Copied!");
      return true;
    } catch {
      // fallback path for non-secure contexts / older browsers
      const ta = document.createElement("textarea");
      ta.value = txt;
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      flash(btn, ok ? "Copied!" : "Copy failed");
      return ok;
    }
  }

  function flash(btn, msg) {
    if (!btn) return;
    const old = btn.getAttribute("aria-label") || "";
    btn.classList.add("copied");
    btn.setAttribute("aria-label", msg);
    setTimeout(() => {
      btn.classList.remove("copied");
      btn.setAttribute("aria-label", old);
    }, 900);
  }

  class GhosttyUI {
    constructor() {
      this.src = document.querySelector("#ghostty-src");
      this.out = document.querySelector("#ghostty-css");
      this.btn = document.querySelector("#ghostty-generate");
      this.copy = document.querySelector("#lab-copy");

      this.K_IN = "ghostty:src";
      this.K_OUT = "ghostty:css:text";

      // hydrate inputs and output
      if (this.src) {
        const sIn = sessionStorage.getItem(this.K_IN);
        if (sIn) this.src.value = sIn;
        this.src.addEventListener("input", () =>
          sessionStorage.setItem(this.K_IN, this.src.value),
        );
      }
      if (this.out) {
        const sOut = sessionStorage.getItem(this.K_OUT);
        if (sOut) Ghostty.renderOutput(this.out, sOut);
      }

      // wire buttons (individually; no early bail)
      if (this.btn) this.btn.addEventListener("click", () => this.generate());

      if (this.copy) {
        this.copy.addEventListener("click", async () => {
          // If nothing rendered yet but we have input, generate now.
          if (
            this.out &&
            !this.out.textContent.trim() &&
            this.src?.value.trim()
          ) {
            this.generate();
          }
          const txt = this.out ? Ghostty.collectText(this.out).trim() : "";
          if (!txt) return flash(this.copy, "Nothing to copy");
          await copyText(txt, this.copy);
        });
      }
    }

    generate() {
      const vars = Ghostty.toVars(this.src?.value || "");
      const text = Ghostty.formatRoot(vars);
      if (this.out) Ghostty.renderOutput(this.out, text);
      sessionStorage.setItem(this.K_OUT, text);
    }
  }

  /* --------------------------------- Mixer --------------------------------- */
  function initMixerWithDropdown() {
    const dropdown = new Dropdown("#algo-dropdown", {
      items: {
        srgb: "sRGB γ-encoded",
        linear: "Linear-light sRGB",
        oklab: "Oklab",
        okhsv: "OkHSV",
        cam16ucs: "CAM16-UCS",
        km_sub: "Kubelka–Munk",
      },
      value: "srgb",
      itemClass: "algo-item",
    });
    const form = $("#mixform");
    if (!form) return;

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const a = $("#colA").value.replace(/^#/, "");
      const b = $("#colB").value.replace(/^#/, "");
      const n = +$("#steps").value;

      const resp = await fetch(
        `/mix?${new URLSearchParams({
          algo: dropdown.value,
          a,
          b,
          n,
        })}`,
      );
      const data = await resp.json();

      const container = $("#swatches");
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

        [hexEl, rgbEl].forEach((el) => {
          el.addEventListener("click", async (ev) => {
            ev.stopPropagation();
            const txt = el === hexEl ? hex : `rgb${el.textContent}`;
            await navigator.clipboard.writeText(txt);

            const wrapper = el.parentNode;
            const tick = document.createElement("span");
            tick.className = "tick";
            tick.textContent = "✓";
            wrapper.appendChild(tick);
            setTimeout(() => tick.classList.add("fade"), 1000);
            tick.addEventListener("transitionend", () => tick.remove());
          });
        });

        sw.append(chip, wrapHex, wrapRgb);
        container.append(sw);
      });

      // chaos bg
      const target = $("#chaos");
      if (target && data.length) {
        const first = data[0],
          last = data[data.length - 1],
          mid = data[Math.floor((data.length - 1) / 2)];
        target.style.backgroundImage = `radial-gradient(circle, ${last} 0%, ${mid} 50%, ${first} 100%)`;
      }
    });

    // auto-mix on load (optional)
    setTimeout(() => form.dispatchEvent(new Event("submit")), 100);
  }

  /* --------------------------------- boot ---------------------------------- */
  document.addEventListener("DOMContentLoaded", () => {
    new NumberField(".number-field");
    new GhosttyUI();

    const savedFmt = sessionStorage.getItem("lab:format") || "css";
    new Dropdown("#format-dropdown", {
      items: { css: "CSS", nvim: "nvim" },
      value: savedFmt,
      itemClass: "algo-item",
      onSelect: (v) => sessionStorage.setItem("lab:format", v),
      // no triggerSel/menuSel needed if your HTML has data-trigger / data-menu
    });

    initMixerWithDropdown();

    new Tabs(
      [
        { btn: "#tab-btn-mix", panel: "#tab-mix" },
        { btn: "#tab-btn-blank", panel: "#tab-blank" },
      ],
      { storageKey: "activeTab", indicator: ".tab-indicator" },
    );
  });
})();
