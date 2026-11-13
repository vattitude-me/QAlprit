(function () {
  // Mirrors real runs from examples/mda-sample-run/results.json (Android) & 20261113_172847_ios_mda (iOS)
  const PLATFORM_RUNS = {
    android: {
      runId: "20261113_135128_android_mda",
      reportUrl: "sample-report.html",
      reportLabel: "Open Full 5/5 Android Report (104 steps)",
      kpis: { rate: "100%", total: 5, passed: 5, failed: 0, flaky: 0, duration: "2m 20.0s" },
      tests: [
        {
          id: "MDA-001",
          title: "Standard user login via menu and session verification",
          status: "passed",
          flaky: false,
          tags: ["mda", "auth", "smoke"],
          duration: "23.82s",
          expected: "User signs in cleanly with secret masking/redaction, session shows Log Out in drawer, and teardown signs out.",
          steps: [
            {
              idx: 7,
              status: "passed",
              action: "type",
              flow: "mda/login",
              duration: "1.34s",
              desc: "Type password into mda_login.password_field (secret: true)",
              redacted: 1,
              screen: "login",
            },
            {
              idx: 13,
              status: "passed",
              action: "assert_visible",
              flow: null,
              duration: "0.78s",
              desc: "Assert visible mda_nav.menu_logout -> screenshot mda_logged_in_menu",
              redacted: 0,
              screen: "catalog",
            },
          ],
        },
        {
          id: "MDA-002",
          title: "Locked-out user sees account locked error banner",
          status: "passed",
          flaky: false,
          tags: ["mda", "auth", "negative"],
          duration: "14.87s",
          expected: "Attempting to log in as a locked-out user remains on Login and displays the locked-out error message.",
          steps: [
            {
              idx: 11,
              status: "passed",
              action: "screenshot",
              flow: null,
              duration: "0.91s",
              desc: "Verify locked-out banner with password field black-filled in memory",
              redacted: 1,
              imageSrc: "previews/mda_02_locked_out.jpg",
              screen: "locked",
            },
          ],
        },
        {
          id: "MDA-003",
          title: "Sort catalog by price descending, inspect top product, and scroll catalog",
          status: "passed",
          flaky: false,
          tags: ["mda", "catalog", "navigation"],
          duration: "25.83s",
          expected: "Sorting reorders highest-priced item first, tapping opens Fleece Jacket detail, back returns to catalog, and scrolling locates Sauce Labs Onesie.",
          steps: [
            {
              idx: 13,
              status: "passed",
              action: "scroll_until_visible",
              flow: null,
              duration: "4.12s",
              desc: "Scroll down until mda_catalog.product_by_title ('Sauce Labs Onesie') visible",
              redacted: 0,
              screen: "catalog",
            },
          ],
        },
        {
          id: "MDA-004",
          title: "End-to-end authenticated purchase checkout with sensitive data redaction",
          status: "passed",
          flaky: false,
          tags: ["mda", "checkout", "e2e", "regression"],
          duration: "59.00s",
          expected: "Complete login, item selection, cart review, shipping, redacted payment entry, order review, and checkout completion succeed.",
          steps: [
            {
              idx: 36,
              status: "passed",
              action: "screenshot",
              flow: "mda/login",
              duration: "1.18s",
              desc: "Verify payment form with Card Number & Security Code (sensitive: true) redacted",
              redacted: 2,
              imageSrc: "previews/mda_04_payment_redacted.jpg",
              screen: "payment",
            },
            {
              idx: 42,
              status: "passed",
              action: "assert_visible",
              flow: null,
              duration: "0.84s",
              desc: "Assert visible mda_complete.thank_you -> screenshot mda_order_complete",
              redacted: 0,
              screen: "complete",
            },
          ],
        },
        {
          id: "MDA-005",
          title: "Cart quantity update and item removal",
          status: "passed",
          flaky: false,
          tags: ["mda", "cart", "smoke"],
          duration: "16.30s",
          expected: "Adding a product updates the cart badge, cart reflects price and count, and removing the item empties the cart.",
          steps: [
            {
              idx: 11,
              status: "passed",
              action: "assert_visible",
              flow: null,
              duration: "0.72s",
              desc: "Remove item -> verify mda_cart.empty_title ('No Items')",
              redacted: 0,
              screen: "complete",
            },
          ],
        },
      ],
    },
    ios: {
      runId: "20261113_172847_ios_mda",
      reportUrl: "sample-report-ios.html",
      reportLabel: "Open Full 3/3 iOS Report (80 steps)",
      kpis: { rate: "100%", total: 3, passed: 3, failed: 0, flaky: 0, duration: "1m 56.5s" },
      tests: [
        {
          id: "MDA-001",
          title: "Standard user login via menu and session verification",
          status: "passed",
          flaky: false,
          tags: ["mda", "auth", "smoke"],
          duration: "29.62s",
          expected: "User signs in cleanly on iPhone 17 (iOS 26.5) with secret masking/redaction, More menu shows Log Out, and teardown signs out.",
          steps: [
            {
              idx: 7,
              status: "passed",
              action: "type",
              flow: "mda/login",
              duration: "1.48s",
              desc: "Type password into mda_login.password_field (secret: true) -> minimize iOS keyboard",
              redacted: 1,
              screen: "login",
            },
            {
              idx: 15,
              status: "passed",
              action: "screenshot",
              flow: null,
              duration: "0.86s",
              desc: "Verify authenticated More menu on iOS with FaceID & Log Out entries",
              redacted: 0,
              imageSrc: "previews/mda_ios_01_logged_in.jpg",
              screen: "catalog",
            },
          ],
        },
        {
          id: "MDA-004",
          title: "End-to-end authenticated purchase checkout with sensitive data redaction",
          status: "passed",
          flaky: false,
          tags: ["mda", "checkout", "e2e", "regression"],
          duration: "1m 16.6s",
          expected: "Complete login, cart, shipping (with iOS hide_keyboard steps), redacted payment entry, review, and checkout completion succeed.",
          steps: [
            {
              idx: 37,
              status: "passed",
              action: "screenshot",
              flow: "mda/login",
              duration: "1.12s",
              desc: "Verify iOS payment form with Card Number* & Security Code* black-filled in memory",
              redacted: 2,
              imageSrc: "previews/mda_ios_04_payment_redacted.jpg",
              screen: "payment",
            },
            {
              idx: 48,
              status: "passed",
              action: "assert_visible",
              flow: null,
              duration: "0.94s",
              desc: "Place Order on iOS -> verify Thank you for your order",
              redacted: 0,
              screen: "complete",
            },
          ],
        },
        {
          id: "MDA-005",
          title: "Cart quantity update and item removal",
          status: "passed",
          flaky: false,
          tags: ["mda", "cart", "smoke"],
          duration: "10.21s",
          expected: "Adding a product shows 1 Items and $29.99 in Cart, and Remove Item empties the cart.",
          steps: [
            {
              idx: 11,
              status: "passed",
              action: "assert_not_visible",
              flow: null,
              duration: "0.69s",
              desc: "Tap Remove Item -> verify cart total cleared cleanly on iOS",
              redacted: 0,
              screen: "complete",
            },
          ],
        },
      ],
    },
  };

  function renderPhoneSvg(kind) {
    const header = `<rect x="0" y="0" width="220" height="420" rx="22" fill="#0f172a" stroke="#334155" stroke-width="2"/>
      <rect x="10" y="14" width="200" height="392" rx="14" fill="#f8fafc"/>
      <rect x="78" y="20" width="64" height="8" rx="4" fill="#cbd5e1"/>`;
    if (kind === "login" || kind === "locked") {
      return `<svg viewBox="0 0 220 420" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Redacted login mobile screenshot">
        ${header}
        <text x="24" y="64" font-family="sans-serif" font-size="14" font-weight="700" fill="#0f172a">Sign In</text>
        <rect x="24" y="82" width="172" height="30" rx="6" fill="#ffffff" stroke="#cbd5e1"/>
        <text x="32" y="101" font-family="monospace" font-size="10" fill="#334155">qa.demo@example.com</text>
        <rect x="24" y="124" width="172" height="32" rx="6" fill="#000000"/>
        <text x="110" y="144" text-anchor="middle" font-family="monospace" font-size="9" font-weight="700" fill="#fbbf24">REDACTED BY PILLOW</text>
        ${kind === "locked" ? `<rect x="24" y="168" width="172" height="30" rx="6" fill="#fee2e2" stroke="#fca5a5"/><text x="32" y="187" font-family="sans-serif" font-size="9" font-weight="700" fill="#b91c1c">User is locked out.</text>` : ""}
        <rect x="24" y="212" width="172" height="34" rx="8" fill="#0f766e"/>
        <text x="110" y="233" text-anchor="middle" font-family="sans-serif" font-size="11" font-weight="700" fill="#ffffff">Log In</text>
      </svg>`;
    }
    if (kind === "payment") {
      return `<svg viewBox="0 0 220 420" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Redacted payment card mobile screenshot">
        ${header}
        <text x="24" y="64" font-family="sans-serif" font-size="14" font-weight="700" fill="#0f172a">Payment Method</text>
        <rect x="24" y="82" width="172" height="28" rx="6" fill="#ffffff" stroke="#cbd5e1"/>
        <text x="32" y="100" font-family="sans-serif" font-size="10" fill="#334155">Rebecca Winter</text>
        <rect x="24" y="120" width="172" height="32" rx="6" fill="#000000"/>
        <text x="110" y="140" text-anchor="middle" font-family="monospace" font-size="9" font-weight="700" fill="#fbbf24">CARD # REDACTED</text>
        <rect x="24" y="164" width="82" height="28" rx="6" fill="#ffffff" stroke="#cbd5e1"/>
        <text x="32" y="182" font-family="monospace" font-size="10" fill="#334155">03/28</text>
        <rect x="114" y="164" width="82" height="28" rx="6" fill="#000000"/>
        <text x="155" y="182" text-anchor="middle" font-family="monospace" font-size="9" font-weight="700" fill="#fbbf24">CVV</text>
        <rect x="24" y="212" width="172" height="34" rx="8" fill="#0f766e"/>
        <text x="110" y="233" text-anchor="middle" font-family="sans-serif" font-size="11" font-weight="700" fill="#ffffff">Review Order</text>
      </svg>`;
    }
    if (kind === "webview_fail") {
      return `<svg viewBox="0 0 220 420" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="WebView failure capture">
        ${header}
        <rect x="20" y="48" width="180" height="32" rx="6" fill="#fef3c7" stroke="#f59e0b"/>
        <text x="110" y="68" text-anchor="middle" font-family="monospace" font-size="9" font-weight="700" fill="#92400e">WebView Detected (C8)</text>
        <rect x="20" y="92" width="180" height="250" rx="8" fill="#ffffff" stroke="#ef4444" stroke-width="2" stroke-dasharray="5,4"/>
        <text x="110" y="142" text-anchor="middle" font-family="sans-serif" font-size="12" font-weight="700" fill="#0f172a">3-D Secure Challenge</text>
        <rect x="34" y="164" width="152" height="30" rx="6" fill="#000000"/>
        <text x="110" y="183" text-anchor="middle" font-family="monospace" font-size="8.5" font-weight="700" fill="#fbbf24">OTP REDACTED</text>
        <text x="110" y="324" text-anchor="middle" font-family="monospace" font-size="9" fill="#b91c1c">FAIL: Native locator timeout</text>
      </svg>`;
    }
    const title = kind === "complete" ? "Checkout Complete" : "Product Catalog";
    const sub = kind === "complete" ? "Thank you for your order!" : "Sauce Labs Backpack · $29.99";
    return `<svg viewBox="0 0 220 420" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${title} screenshot">
      ${header}
      <text x="24" y="64" font-family="sans-serif" font-size="14" font-weight="700" fill="#0f172a">${title}</text>
      <rect x="24" y="84" width="172" height="120" rx="10" fill="#dcfce7" stroke="#86efac"/>
      <text x="110" y="148" text-anchor="middle" font-family="sans-serif" font-size="11" font-weight="700" fill="#15803d">${sub}</text>
    </svg>`;
  }

  const container = document.getElementById("demoTestsContainer");
  const tagSelect = document.getElementById("tagFilter");
  const searchBox = document.getElementById("searchBox");
  const toggleAllBtn = document.getElementById("toggleAllBtn");
  const lightbox = document.getElementById("proofLightbox");
  const lbTitle = document.getElementById("lbTitle");
  const lbMedia = document.getElementById("lbMedia");
  const lbCaption = document.getElementById("lbCaption");
  const lbClose = document.getElementById("lbClose");
  const liveStatus = document.getElementById("liveStatus");

  const runIdCode = document.getElementById("runIdCode");
  const runFullLink = document.getElementById("runFullLink");
  const kpiRate = document.getElementById("kpiRate");
  const kpiTotal = document.getElementById("kpiTotal");
  const kpiPassed = document.getElementById("kpiPassed");
  const kpiFailed = document.getElementById("kpiFailed");
  const kpiFlaky = document.getElementById("kpiFlaky");
  const kpiDuration = document.getElementById("kpiDuration");
  const statusButtons = Array.from(document.querySelectorAll("#statusFilters .btn-filter"));

  let activePlatform = "android";
  let activeStatus = "all";
  let expandedAll = false;

  function renderRun() {
    const run = PLATFORM_RUNS[activePlatform];
    runIdCode.textContent = run.runId;
    runFullLink.href = run.reportUrl;
    runFullLink.textContent = run.reportLabel;
    kpiRate.textContent = run.kpis.rate;
    kpiTotal.textContent = String(run.kpis.total);
    kpiPassed.textContent = String(run.kpis.passed);
    kpiFailed.textContent = String(run.kpis.failed);
    kpiFlaky.textContent = String(run.kpis.flaky);
    kpiDuration.textContent = run.kpis.duration;

    const counts = {
      all: run.tests.length,
      passed: run.tests.filter((t) => t.status === "passed").length,
      failed: run.tests.filter((t) => t.status === "failed").length,
      flaky: run.tests.filter((t) => t.flaky).length,
    };
    const labels = { all: "All", passed: "Passed", failed: "Failed", flaky: "Flaky" };
    statusButtons.forEach((btn) => {
      const key = btn.dataset.status;
      btn.textContent = `${labels[key]} (${counts[key]})`;
    });

    renderTests(run.tests);
    applyFilters();
  }

  function renderTests(tests) {
    container.innerHTML = tests.map((t) => `
      <details class="test-card"
        data-status="${t.status}"
        data-flaky="${t.flaky ? "true" : "false"}"
        data-tags="${t.tags.join(",")}"
        data-search="${(t.id + " " + t.title).toLowerCase()}"
        ${t.status === "failed" ? "open" : ""}>
        <summary class="test-summary">
          <div class="test-left">
            <span class="badge b-${t.status}">${t.status}</span>
            <span class="test-id">${t.id}</span>
            <span class="test-title">${t.title}</span>
            ${t.flaky ? `<span class="badge b-flaky">Flaky (${t.attempts} attempts)</span>` : ""}
            ${t.webview ? `<span class="badge b-flaky">WebView</span>` : ""}
            ${t.tags.map((tag) => `<span class="badge b-tag">${tag}</span>`).join("")}
          </div>
          <div class="test-right">${t.duration}</div>
        </summary>
        <div class="test-body">
          <p style="margin:0 0 10px; font-size:13px; color:var(--muted);"><strong>Expected:</strong> ${t.expected}</p>
          ${t.failure ? `<div class="error-box"><strong>Failure:</strong> ${t.failure}</div>` : ""}
          <div class="step-list">
            ${t.steps.map((s) => `
              <div class="step-row">
                <div class="step-num ${s.status}">${s.idx}</div>
                <div>
                  <div class="step-desc">${s.desc}</div>
                  <div class="step-meta">
                    <span>${s.action}</span>
                    <span>· ${s.duration}</span>
                    ${s.flow ? `<span class="badge b-flow">flow: ${s.flow}</span>` : ""}
                    ${s.redacted ? `<span class="badge b-redact">Redacted ${s.redacted}</span>` : ""}
                  </div>
                  ${t.xmlPreview && s.status === "failed" ? `<pre class="xml-box">${t.xmlPreview.replace(/</g, "&lt;")}</pre>` : ""}
                </div>
                <button type="button" class="thumb-btn" data-screen="${s.screen}" data-img="${s.imageSrc || ""}" data-title="${t.id} · Step ${s.idx}" data-caption="${s.desc}" aria-label="Enlarge screenshot for ${t.id} step ${s.idx}">
                  ${s.imageSrc ? `<img src="${s.imageSrc}" alt="${t.id} step ${s.idx} redacted screenshot" width="76" loading="lazy" />` : renderPhoneSvg(s.screen)}
                </button>
              </div>`).join("")}
          </div>
        </div>
      </details>
    `).join("");

    container.querySelectorAll(".thumb-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        lbTitle.textContent = btn.dataset.title || "Step Proof";
        if (btn.dataset.img) {
          lbMedia.innerHTML = `<img src="${btn.dataset.img}" alt="${btn.dataset.caption || "Step screenshot"}" />`;
        } else {
          lbMedia.innerHTML = renderPhoneSvg(btn.dataset.screen);
        }
        lbCaption.textContent = btn.dataset.caption || "";
        lightbox.showModal();
      });
    });
  }

  function applyFilters() {
    const tagVal = tagSelect.value;
    const q = searchBox.value.trim().toLowerCase();
    const cards = container.querySelectorAll(".test-card");
    cards.forEach((card) => {
      const st = card.dataset.status;
      const isFlaky = card.dataset.flaky === "true";
      const tags = card.dataset.tags.split(",");
      const hay = card.dataset.search;
      const matchStatus = activeStatus === "all" || (activeStatus === "flaky" ? isFlaky : st === activeStatus);
      const matchTag = tagVal === "all" || tags.includes(tagVal);
      const matchQ = !q || hay.includes(q);
      card.style.display = matchStatus && matchTag && matchQ ? "" : "none";
    });
  }

  document.querySelectorAll("#statusFilters .btn-filter").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#statusFilters .btn-filter").forEach((b) => {
        b.classList.remove("active");
        b.setAttribute("aria-pressed", "false");
      });
      btn.classList.add("active");
      btn.setAttribute("aria-pressed", "true");
      activeStatus = btn.dataset.status;
      applyFilters();
    });
  });

  tagSelect.addEventListener("change", applyFilters);
  searchBox.addEventListener("input", applyFilters);
  toggleAllBtn.addEventListener("click", () => {
    expandedAll = !expandedAll;
    container.querySelectorAll(".test-card").forEach((c) => {
      if (c.style.display !== "none") c.open = expandedAll;
    });
    toggleAllBtn.textContent = expandedAll ? "Collapse All" : "Expand All";
  });

  lbClose.addEventListener("click", () => lightbox.close());
  if (!("closedBy" in HTMLDialogElement.prototype)) {
    const outside = (e) => {
      const r = lightbox.getBoundingClientRect();
      return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
    };
    let pressedOutside = false;
    lightbox.addEventListener("pointerdown", (e) => { pressedOutside = outside(e); });
    lightbox.addEventListener("pointerup", (e) => { if (pressedOutside && outside(e)) lightbox.close(); });
  }

  // Shared accessible tab initializer (selection follows focus + Arrow keys + beforematch)
  function initTabGroup(tablistSelector, onSelect) {
    const tabs = Array.from(document.querySelectorAll(`${tablistSelector} [role='tab']`));
    if (!tabs.length) return;
    const panels = tabs.map((tab) => document.querySelector(`[aria-labelledby="${tab.id}"]`));

    function selectTab(targetTab, moveFocus) {
      tabs.forEach((tab, idx) => {
        const selected = tab === targetTab;
        tab.setAttribute("aria-selected", String(selected));
        const panel = panels[idx];
        if (panel) {
          if (selected) {
            panel.removeAttribute("hidden");
            panel.setAttribute("tabindex", "0");
          } else {
            panel.setAttribute("hidden", "until-found");
            panel.removeAttribute("tabindex");
          }
        }
      });
      targetTab.scrollIntoView({ block: "nearest", inline: "nearest" });
      if (moveFocus) targetTab.focus();
      if (onSelect) onSelect(targetTab);
    }

    tabs.forEach((tab, idx) => {
      tab.addEventListener("click", () => selectTab(tab, false));
      tab.addEventListener("keydown", (e) => {
        if (e.key === "ArrowRight") {
          e.preventDefault();
          selectTab(tabs[(idx + 1) % tabs.length], true);
        } else if (e.key === "ArrowLeft") {
          e.preventDefault();
          selectTab(tabs[(idx - 1 + tabs.length) % tabs.length], true);
        }
      });
    });

    panels.forEach((panel, idx) => {
      if (panel) {
        panel.addEventListener("beforematch", () => selectTab(tabs[idx], false));
      }
    });
  }

  initTabGroup(".terminal-tabs");
  initTabGroup(".studio-tablist");
  initTabGroup(".platform-tablist", (tab) => {
    const nextPlatform = tab.dataset.platform;
    if (nextPlatform && PLATFORM_RUNS[nextPlatform]) {
      activePlatform = nextPlatform;
      expandedAll = false;
      toggleAllBtn.textContent = "Expand All";
      renderRun();
    }
  });

  // Copy buttons
  document.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const text = btn.dataset.copy || "";
      try {
        await navigator.clipboard.writeText(text);
        const prev = btn.textContent;
        btn.textContent = "Copied";
        liveStatus.textContent = "Copied command to clipboard";
        setTimeout(() => { btn.textContent = prev; }, 1600);
      } catch (_err) {
        liveStatus.textContent = "Copy failed";
      }
    });
  });

  renderRun();
})();
