(function () {
  // Demo sanitized run illustrating QAlprit's Proof Report & Pillow in-memory redaction
  const DEMO_TESTS = [
    {
      id: "MDA-001",
      title: "Standard user login via menu and session verification",
      status: "passed",
      flaky: false,
      tags: ["mda", "auth", "smoke"],
      duration: "6.42s",
      expected: "User signs in cleanly with secret masking/redaction and session shows Log Out.",
      steps: [
        {
          idx: 1,
          status: "passed",
          action: "type",
          flow: "mda/login",
          duration: "1.12s",
          desc: "Type password into mda_login.password_field (secret: true)",
          redacted: 1,
          screen: "login",
        },
        {
          idx: 2,
          status: "passed",
          action: "tap",
          flow: "mda/login",
          duration: "0.94s",
          desc: "Tap mda_login.login_button -> verify mda_catalog.title",
          redacted: 0,
          screen: "catalog",
        },
      ],
    },
    {
      id: "MDA-004",
      title: "End-to-end authenticated purchase checkout with card & CVV redaction",
      status: "passed",
      flaky: false,
      tags: ["mda", "checkout", "auth"],
      duration: "14.80s",
      expected: "Card number and CVV bounding boxes are black-filled in memory before PNG write.",
      steps: [
        {
          idx: 1,
          status: "passed",
          action: "type",
          flow: null,
          duration: "1.45s",
          desc: "Type card & security code into mda_payment (sensitive: true)",
          redacted: 2,
          screen: "payment",
        },
        {
          idx: 2,
          status: "passed",
          action: "assert_visible",
          flow: null,
          duration: "0.78s",
          desc: "Assert visible mda_complete.thank_you -> screenshot mda_order_complete",
          redacted: 0,
          screen: "complete",
        },
      ],
    },
    {
      id: "MDA-003",
      title: "Sort catalog by price descending and scroll to item",
      status: "passed",
      flaky: true,
      attempts: 2,
      tags: ["mda", "catalog"],
      duration: "9.15s",
      expected: "Passed on retry #2 after catalog animation settled; flagged flaky.",
      steps: [
        {
          idx: 1,
          status: "passed",
          action: "scroll_until_visible",
          flow: null,
          duration: "2.31s",
          desc: "Scroll down until mda_catalog.product_by_title ('Sauce Labs Onesie') visible",
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
      duration: "5.21s",
      expected: "Displays locked-out validation banner with password field redacted.",
      steps: [
        {
          idx: 1,
          status: "passed",
          action: "assert_text",
          flow: null,
          duration: "0.86s",
          desc: "Assert mda_login.error_banner contains 'locked out'",
          redacted: 1,
          screen: "locked",
        },
      ],
    },
    {
      id: "SSO-009",
      title: "Checkout 3DS challenge flags embedded WebView on failure",
      status: "failed",
      flaky: false,
      webview: true,
      tags: ["checkout", "webview"],
      duration: "12.90s",
      failure: "Timeout waiting 12.0s for native locator 'checkout.confirm_btn'. Embedded android.webkit.WebView detected in page source.",
      expected: "Native confirmation screen renders after payment submit.",
      xmlPreview: "<hierarchy>\n  <android.webkit.WebView bounds=\"[0,120][1080,2240]\">\n    <android.widget.TextView text=\"3-D Secure Challenge\" />\n  </android.webkit.WebView>\n</hierarchy>",
      steps: [
        {
          idx: 1,
          status: "failed",
          action: "wait_for_visible",
          flow: null,
          duration: "12.04s",
          desc: "Wait for visible checkout.confirm_btn (timeout 12s)",
          redacted: 2,
          screen: "webview_fail",
        },
      ],
    },
  ];

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

  let activeStatus = "all";
  let expandedAll = false;

  function renderTests() {
    container.innerHTML = DEMO_TESTS.map((t) => `
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
                <button type="button" class="thumb-btn" data-screen="${s.screen}" data-title="${t.id} · Step ${s.idx}" data-caption="${s.desc}" aria-label="Enlarge screenshot for ${t.id} step ${s.idx}">
                  ${renderPhoneSvg(s.screen)}
                </button>
              </div>`).join("")}
          </div>
        </div>
      </details>
    `).join("");

    container.querySelectorAll(".thumb-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        lbTitle.textContent = btn.dataset.title || "Step Proof";
        lbMedia.innerHTML = renderPhoneSvg(btn.dataset.screen);
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

  // Accessible Spec Studio Tabs (selection follows activation + Arrow keys + beforematch)
  const studioTabs = Array.from(document.querySelectorAll(".studio-tablist [role='tab']"));
  const studioPanels = studioTabs.map((tab) => document.querySelector(`[aria-labelledby="${tab.id}"]`));

  function selectStudioTab(targetTab, moveFocus) {
    studioTabs.forEach((tab, idx) => {
      const selected = tab === targetTab;
      tab.setAttribute("aria-selected", String(selected));
      const panel = studioPanels[idx];
      if (selected) {
        panel.removeAttribute("hidden");
        panel.setAttribute("tabindex", "0");
      } else {
        panel.setAttribute("hidden", "until-found");
        panel.removeAttribute("tabindex");
      }
    });
    if (moveFocus) targetTab.focus();
  }

  studioTabs.forEach((tab, idx) => {
    tab.addEventListener("click", () => selectStudioTab(tab, false));
    tab.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") {
        e.preventDefault();
        selectStudioTab(studioTabs[(idx + 1) % studioTabs.length], true);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        selectStudioTab(studioTabs[(idx - 1 + studioTabs.length) % studioTabs.length], true);
      }
    });
  });

  studioPanels.forEach((panel, idx) => {
    panel.addEventListener("beforematch", () => selectStudioTab(studioTabs[idx], false));
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

  renderTests();
})();
