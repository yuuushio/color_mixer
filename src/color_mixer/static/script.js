document.querySelectorAll("input").forEach((el) => {
  el.setAttribute("autocomplete", "off");
  el.setAttribute("spellcheck", "false");
  el.setAttribute("autocorrect", "off");
  el.setAttribute("autocapitalize", "off");
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

  // Initialize everything
  buildMenu();
  updateTrigger();
  // auto-mix once on load
  setTimeout(() => form.dispatchEvent(new Event("submit")), 100);
});
