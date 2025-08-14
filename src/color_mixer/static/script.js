(() => {
  "use strict";

  /* =============================== Utilities =============================== */

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  // Canonicalize to "#rrggbb"; accept "#rgb" or "#rrggbb". Throw on invalid.
  function canonHex(input) {
    if (!input) throw new Error("empty color");
    let s = String(input).trim().toLowerCase();
    if (s[0] !== "#") s = "#" + s;
    const raw = s.slice(1);
    if (/^[0-9a-f]{3}$/.test(raw)) {
      const r = raw
        .split("")
        .map((c) => c + c)
        .join("");
      return "#" + r;
    }
    if (/^[0-9a-f]{6}$/.test(raw)) return s.slice(0, 7);
    throw new Error(`invalid hex: "${input}"`);
  }

  // Build a radial "chaos" background from three swatches
  function setChaosBackground(node, palette) {
    if (!node || !palette?.length) return;
    const first = palette[0];
    const last = palette[palette.length - 1];
    const mid = palette[Math.floor((palette.length - 1) / 2)];
    node.style.backgroundImage = `radial-gradient(circle, ${last} 0%, ${mid} 50%, ${first} 100%)`;
  }

  // Safe JSON parse from fetch Response; fallbacks to plain text if needed
  async function parseJSON(resp) {
    try {
      return await resp.json();
    } catch {
      try {
        const txt = await resp.text();
        return { error: txt || `HTTP ${resp.status}` };
      } catch {
        return { error: `HTTP ${resp.status}` };
      }
    }
  }

  /* ================================ LRU Cache ============================== */

  class LRU {
    constructor(limit = 32) {
      this.limit = limit;
      this.map = new Map();
    }
    _touch(k, v) {
      if (this.map.has(k)) this.map.delete(k);
      this.map.set(k, v);
      if (this.map.size > this.limit) {
        const firstKey = this.map.keys().next().value;
        this.map.delete(firstKey);
      }
    }
    get(k) {
      if (!this.map.has(k)) return undefined;
      const v = this.map.get(k);
      this._touch(k, v);
      return v;
    }
    set(k, v) {
      this._touch(k, v);
      return v;
    }
  }

  /* ================================ Dropdown ============================== */

  // Minimal, accessible dropdown: click-to-open, esc-to-close, arrow-key travel.
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
      if (!this.trigger || !this.menu) return;

      this._items = Array.isArray(items)
        ? items.map(({ value, label }) => ({ value, label }))
        : Object.entries(items).map(([value, label]) => ({ value, label }));

      this._value = null;

      this._buildMenu();
      this.set(value ?? this._items[0]?.value ?? null, { silent: true });

      this._onDocClick = (e) => {
        if (!this.root.contains(e.target)) this.close();
      };
      this._onDocKey = (e) => {
        if (e.key === "Escape") return void this.close();
        if (!this.root.classList.contains(this.openClass)) return;
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault();
          const btns = this._buttons || [];
          if (!btns.length) return;
          const activeIdx = btns.findIndex((b) =>
            b.classList.contains("active"),
          );
          const i = activeIdx < 0 ? 0 : activeIdx;
          const j =
            e.key === "ArrowDown"
              ? (i + 1) % btns.length
              : (i - 1 + btns.length) % btns.length;
          btns[j].focus();
        }
        if (e.key === "Enter" && document.activeElement?.dataset?.value) {
          e.preventDefault();
          this.set(document.activeElement.dataset.value);
        }
      };

      // open/close strictly via trigger; no root-level toggling to avoid surprises
      this.trigger.addEventListener("click", (e) => {
        e.stopPropagation();
        this.toggle();
      });
      this.trigger.setAttribute("aria-haspopup", "listbox");
      this.trigger.setAttribute("aria-expanded", "false");
    }

    _buildMenu() {
      this.menu.innerHTML = "";
      this.menu.setAttribute("role", "listbox");
      for (const { value, label } of this._items) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = this.itemClass;
        btn.dataset.value = value;
        btn.setAttribute("role", "option");
        btn.textContent = label;
        btn.addEventListener("click", (ev) => {
          ev.stopPropagation();
          this.set(value);
        });
        this.menu.appendChild(btn);
      }
      this._buttons = $$(`.${this.itemClass}`, this.menu);
    }

    open() {
      this.root.classList.add(this.openClass);
      this.trigger.setAttribute("aria-expanded", "true");
      document.addEventListener("click", this._onDocClick);
      document.addEventListener("keydown", this._onDocKey);
    }

    close() {
      this.root.classList.remove(this.openClass);
      this.trigger.setAttribute("aria-expanded", "false");
      document.removeEventListener("click", this._onDocClick);
      document.removeEventListener("keydown", this._onDocKey);
    }

    toggle() {
      this.root.classList.contains(this.openClass) ? this.close() : this.open();
    }

    set(value, { silent = false } = {}) {
      if (value == null || value === this._value) return;
      this._value = value;
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

  /* ============================== NumberField ============================== */

  class NumberField {
    constructor(rootSel = ".number-field", { forceMin, forceMax } = {}) {
      this.root = $(rootSel);
      if (!this.root) return;
      this.input = this.root.querySelector("input[type=number]");
      this.up = this.root.querySelector(".step-increment-btn");
      this.down = this.root.querySelector(".step-decrement-btn");
      if (!this.input || !this.up || !this.down) return;

      // Optionally override bounds (keep UI and backend in sync)
      if (typeof forceMin === "number") this.input.min = String(forceMin);
      if (typeof forceMax === "number") this.input.max = String(forceMax);

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
        const step = +this.input.step || 1;
        const next = this._clamp((+this.input.value || 0) + dir * step);
        this.input.value = String(next);
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

  /* =================================== Tabs ================================= */

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

      for (const { btn } of config) {
        const el = $(btn);
        if (!el) continue;
        el.addEventListener("click", (e) => {
          e.preventDefault();
          const id = e.currentTarget.id.replace("tab-btn", "tab");
          this.activate(id);
        });
      }

      // Align indicator on first paint & resize
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
        const b = $(btn);
        b?.classList.toggle("is-active", isActive);
        b?.setAttribute("aria-selected", String(isActive));
        const p = $(panel);
        if (p) {
          p.classList.toggle("is-active", isActive);
          p.toggleAttribute("hidden", !isActive);
        }
        if (isActive) {
          if (this.indicator && immediate)
            this.indicator.style.transition = "none";
          requestAnimationFrame(() => {
            this._moveIndicatorTo(b);
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

  /* ============================== Ghostty helpers =========================== */

  const Ghostty = {
    toVars(text) {
      const kv = {};
      const palette = Array(16).fill(null);
      const lines = text.split(/\r?\n/);

      const normColor = (token) => {
        if (!token) return null;
        let t = token.trim();

        // rgba()/rgb()
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
      const ANSI_NAMES = ["d", "r", "gr", "y", "b", "m", "c", "w"];
      palette.forEach((c, i) => {
        if (!c) return;
        const band = i < 8 ? "n" : "b";
        const letter = ANSI_NAMES[i % 8];
        out.set(`--${band}${letter}`, c);
      });
      return out;
    },

    formatRoot(varsMap) {
      const ANSI_NAMES = ["d", "r", "gr", "y", "b", "m", "c", "w"];
      const ansiOrder = [
        ...ANSI_NAMES.map((l) => `--n${l}`),
        ...ANSI_NAMES.map((l) => `--b${l}`),
      ];
      const xOrder = [
        "--xbg-100",
        "--xbg-200",
        "--xbg-300",
        "--xbg-500",
        "--xbg-600",
        "--xbg-700",
        "--xpink",
        "--xgray-1",
        "--xgray-2",
        "--xgray-3",
        "--xlg-1",
        "--xlg-2",
        "--xlg-3",
        "--xnordblue-1",
        "--xnordblue-2",
      ];
      const order = [
        "--bg",
        "--fg",
        "--cursor",
        "--cursor-text",
        "--selection-bg",
        "--selection-fg",
        ...ansiOrder,
        ...xOrder,
      ];
      const known = [],
        rest = [];
      for (const [k, v] of varsMap.entries())
        (order.includes(k) ? known : rest).push([k, v]);
      known.sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]));
      rest.sort((a, b) => a[0].localeCompare(b[0]));

      return [
        "",
        ...[...known, ...rest].map(([k, v]) => `  ${k}: ${v};`),
        "",
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
          span.style.pointerEvents = "none"; // avoid accidental interactions
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

  /* ================================== Copy ================================= */

  async function copyText(txt, btn) {
    try {
      await navigator.clipboard.writeText(txt);
      flash(btn, "Copied!");
      return true;
    } catch {
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

  /* ================================= Mixer API ============================== */

  const ShadeGen = (() => {
    const cache = new LRU(32);

    function key(aHex, bHex, n, algo) {
      return `${aHex}|${bHex}|${algo}|${n}`;
    }

    return {
      /**
       * Fetch palette between a and b, using algo and n steps. Uses LRU cache.
       * Returns Promise<string[]>
       */
      async fromTo(aHex, bHex = "#000000", n = 27, algo = "oklab") {
        const A = canonHex(aHex);
        const B = canonHex(bHex);
        const steps = Math.max(2, Math.min(512, n | 0));
        const k = key(A, B, steps, algo);
        const hit = cache.get(k);
        if (hit) return hit;

        const params = new URLSearchParams({
          algo,
          a: A.slice(1),
          b: B.slice(1),
          n: String(steps),
        });
        const resp = await fetch(`/mix?${params}`);
        if (!resp.ok) {
          const j = await parseJSON(resp);
          throw new Error(j?.error || `mix failed (${resp.status})`);
        }
        const arr = await resp.json();
        cache.set(k, arr);
        return arr;
      },
    };
  })();

  /* ================================ Ghostty UI ============================== */

  class GhosttyUI {
    constructor() {
      this.src = $("#ghostty-src");
      this.out = $("#ghostty-css");
      this.btn = $("#ghostty-generate");
      this.copy = $("#lab-copy");

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

      // wire buttons
      if (this.btn) this.btn.addEventListener("click", () => this.generate());

      if (this.copy) {
        this.copy.addEventListener("click", async () => {
          if (
            this.out &&
            !this.out.textContent.trim() &&
            this.src?.value.trim()
          ) {
            await this.generate();
          }
          const txt = this.out ? Ghostty.collectText(this.out).trim() : "";
          if (!txt) return flash(this.copy, "Nothing to copy");
          await copyText(txt, this.copy);
        });
      }
    }

    async generate() {
      const vars = Ghostty.toVars(this.src?.value || "");

      const base = vars.get("--bg") || vars.get("--nd");
      if (base) {
        // Prepare tasks in parallel. Helper to pick t from a palette.
        const pick = (arr, t) => arr[Math.round(t * (arr.length - 1))];

        const tasks = [];

        // Dark and light ramps
        const pDark = ShadeGen.fromTo(base, "#000000", 41, "oklab");
        const pLight = ShadeGen.fromTo(base, "#ffffff", 61, "km_sub");
        tasks.push(pDark, pLight);

        // Blue-gray derivative
        const bb = vars.get("--bb"),
          fg = vars.get("--fg");
        const pBlueGray =
          bb && fg
            ? ShadeGen.fromTo(bb, fg, 31, "cam16ucs").then((a) => pick(a, 0.53))
            : Promise.resolve(null);
        tasks.push(pBlueGray);

        // Light grays
        const nd = vars.get("--nd");
        const pLg1 =
          nd && fg
            ? ShadeGen.fromTo(nd, fg, 21, "okhsv").then((a) => pick(a, 0.75))
            : Promise.resolve(null);
        const pLg2 =
          nd && fg
            ? ShadeGen.fromTo(nd, fg, 21, "okhsv").then((a) => pick(a, 0.6))
            : Promise.resolve(null);
        tasks.push(pLg1, pLg2);

        // Singles
        const singles = [
          vars.get("--nr")
            ? ShadeGen.fromTo(vars.get("--nr"), "#ffffff", 61, "oklab").then(
                (a) => pick(a, 0.5),
              )
            : null,
          vars.get("--nc")
            ? ShadeGen.fromTo(vars.get("--nc"), "#d8dee9", 41, "km_sub").then(
                (a) => pick(a, 0.5),
              )
            : null,
          nd
            ? ShadeGen.fromTo(nd, "#ffffff", 41, "okhsv").then((a) =>
                pick(a, 0.13),
              )
            : null,
          nd
            ? ShadeGen.fromTo(nd, "#ffffff", 41, "okhsv").then((a) =>
                pick(a, 0.18),
              )
            : null,
          nd
            ? ShadeGen.fromTo(nd, "#ffffff", 41, "okhsv").then((a) =>
                pick(a, 0.23),
              )
            : null,
          vars.get("--bd") && vars.get("--bw")
            ? ShadeGen.fromTo(
                vars.get("--bd"),
                vars.get("--bw"),
                51,
                "km_sub",
              ).then((a) => pick(a, 0.6))
            : null,
        ].map((p) => p ?? Promise.resolve(null));

        try {
          const [dark, light, blue_gray, lg1, lg2, ...singleVals] =
            await Promise.all([...tasks, ...singles]);

          if (Array.isArray(dark)) {
            const mapDark = { 2: "--bg-500", 4: "--bg-600", 6: "--bg-700" };
            Object.entries(mapDark).forEach(
              ([i, name]) => dark[i] && vars.set(name, dark[i]),
            );
          }
          if (Array.isArray(light)) {
            const mapLight = { 6: "--bg-300", 9: "--bg-200", 11: "--bg-100" };
            Object.entries(mapLight).forEach(
              ([i, name]) => light[i] && vars.set(name, light[i]),
            );
          }
          if (blue_gray) vars.set("--xnordblue-2", blue_gray);
          if (lg1) vars.set("--xlg-2", lg1);
          if (lg2) vars.set("--xlg-3", lg2);

          const names = [
            "--xpink",
            "--xnordblue-1",
            "--xgray-1",
            "--xgray-2",
            "--xgray-3",
            "--xlg-1",
          ];
          names.forEach((name, i) => {
            const v = singleVals[i];
            if (v) vars.set(name, v);
          });
        } catch {
          // tolerate API failures; continue with what we have
        }
      }

      const text = Ghostty.formatRoot(vars);
      if (this.out) Ghostty.renderOutput(this.out, text);
      sessionStorage.setItem(this.K_OUT, text);
    }
  }

  /* --------------------------- Tone schedule control -------------------------- */
  function ToneScheduleUI() {
    const wrap = document.querySelector("#tone-schedule-wrap");
    const labelB = document.querySelector("label.col-b");
    const inputB = document.querySelector("#colB");
    if (!wrap || !labelB || !inputB) return null;

    // Instantiate dropdown on existing HTML
    const saved = sessionStorage.getItem("tone:schedule") || "linear";
    const dd = new Dropdown(wrap.querySelector(".algo-prefix-container"), {
      items: {
        linear: "Linear",
        ease: "Ease",
        shadow: "Shadow",
        highlight: "Highlight",
      },
      value: saved,
      itemClass: "algo-item",
      onSelect: (v) => sessionStorage.setItem("tone:schedule", v),
    });

    // Robust hide/show that beats stylesheet overrides (including !important)
    const setHidden = (el, on) => {
      el.hidden = on;
      el.setAttribute("aria-hidden", on ? "true" : "false");
      try {
        el.inert = !!on;
      } catch {}
      if (on) {
        el.style.setProperty("display", "none", "important");
      } else {
        el.style.removeProperty("display"); // fall back to stylesheet
      }
    };

    const show = () => {
      setHidden(labelB, true);
      inputB.disabled = true;
      setHidden(wrap, false);
    };

    const hide = () => {
      // Ensure the dropdown is closed and listeners removed
      if (dd && typeof dd.close === "function") dd.close();
      setHidden(wrap, true);
      setHidden(labelB, false);
      inputB.disabled = false;
    };

    // Enforce initial hidden state defined in HTML
    if (wrap.hasAttribute("hidden"))
      wrap.style.setProperty("display", "none", "important");

    return {
      get value() {
        return dd?.value || "linear";
      },
      show,
      hide,
    };
  }

  /* ================================== Mixer UI ============================= */

  function initMixerWithDropdown() {
    // Algorithm menu must mirror backend support.
    const dropdown = new Dropdown("#algo-dropdown", {
      items: {
        oklab: "Oklab",
        okhsl: "OkHSL",
        okhsv: "OkHSV",
        mix_hct: "HCT-Mix",
        hct_tone: "Google's HCT",
        srgb: "sRGB γ-encoded",
        linear: "Linear-light sRGB",
        cam16ucs: "CAM16-UCS",
        cam16jmh: "CAM16-JMh",
        km_sub: "Kubelka–Munk",
      },
      value: "oklab",
      itemClass: "algo-item",
    });

    const toneUI = ToneScheduleUI();

    const updateAlgoUI = (algoKey) => {
      if (!toneUI) return;
      algoKey === "hct_tone" ? toneUI.show() : toneUI.hide();
    };

    // Initial toggle on load
    updateAlgoUI(dropdown.value);

    // React to changes from the algo dropdown
    $("#algo-dropdown")?.addEventListener("change", (e) => {
      const val = e.detail?.value || dropdown.value;
      updateAlgoUI(val);
    });

    const form = $("#mixform");
    if (!form) return;

    // Make the "Steps" input reflect backend bounds even if HTML lags behind.
    const stepsInput = $("#steps");
    if (stepsInput) {
      stepsInput.min = "2";
      stepsInput.max = "512";
    }

    form.addEventListener("submit", async (e) => {
      e.preventDefault();

      let a, b, n;
      try {
        a = canonHex($("#colA").value);
        b = canonHex($("#colB").value);
      } catch (err) {
        alert(err.message || "Invalid colour");
        return;
      }
      n = Math.max(2, Math.min(512, +$("#steps").value || 27));

      const params = new URLSearchParams({
        algo: dropdown.value,
        a: a.slice(1),
        b: b.slice(1),
        n: String(n),
      });

      if (dropdown.value === "hct_tone" && toneUI) {
        params.set("schedule", toneUI.value); // "ease" | "linear" | "shadow" | "highlight"
      }

      const resp = await fetch(`/mix?${params}`);
      if (!resp.ok) {
        const j = await parseJSON(resp);
        alert(j?.error || `mix failed (${resp.status})`);
        return;
      }
      const data = await resp.json();

      // Paint swatches with minimal layout churn
      const container = $("#swatches");
      const frag = document.createDocumentFragment();

      for (const hex of data) {
        const [r, g, b] = hex
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
        rgbEl.textContent = `(${r},${g},${b})`;
        wrapRgb.appendChild(rgbEl);

        const bindCopy = (el, formatter) => {
          el.addEventListener("click", async (ev) => {
            ev.stopPropagation();
            const txt = formatter();
            try {
              await navigator.clipboard.writeText(txt);
            } catch {
              // fallback
              await copyText(txt, null);
            }
            const wrapper = el.parentNode;
            const tick = document.createElement("span");
            tick.className = "tick";
            tick.textContent = "✓";
            tick.style.pointerEvents = "none"; // prevent accidental interactions
            wrapper.appendChild(tick);
            // fade out via CSS transition; remove after
            setTimeout(() => tick.classList.add("fade"), 1000);
            tick.addEventListener("transitionend", () => tick.remove(), {
              once: true,
            });
          });
        };

        bindCopy(hexEl, () => hex);
        bindCopy(rgbEl, () => `rgb(${r}, ${g}, ${b})`);

        sw.append(chip, wrapHex, wrapRgb);
        frag.append(sw);
      }

      container.replaceChildren(frag);

      // Update the background
      setChaosBackground($("#chaos"), data);
    });

    // auto-mix on load
    setTimeout(() => form.dispatchEvent(new Event("submit")), 100);
  }

  /* ================================== boot ================================= */

  document.addEventListener("DOMContentLoaded", () => {
    new NumberField(".number-field", { forceMin: 2, forceMax: 512 });
    new GhosttyUI();

    // Persist format choice for Lab tab
    const savedFmt = sessionStorage.getItem("lab:format") || "css";
    new Dropdown("#format-dropdown", {
      items: { css: "CSS", nvim: "nvim" },
      value: savedFmt,
      itemClass: "algo-item",
      onSelect: (v) => sessionStorage.setItem("lab:format", v),
    });

    initMixerWithDropdown();

    // Correct indicator hook: the DOM has .slider, not .tab-indicator
    new Tabs(
      [
        { btn: "#tab-btn-mix", panel: "#tab-mix" },
        { btn: "#tab-btn-blank", panel: "#tab-blank" },
      ],
      { storageKey: "activeTab", indicator: ".slider" },
    );
  });
})();
