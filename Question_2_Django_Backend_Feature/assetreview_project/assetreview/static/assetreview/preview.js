/**
 * preview.js – AJAX row-level re-validation
 *
 * Attaches click handlers to every "Validate" button.
 * Sends a POST request (with CSRF token) to the validate_row_ajax view.
 * Updates the badge and error list in-place without a page reload.
 *
 * Why POST and not GET?
 *   The endpoint performs server-side validation (a stateful operation from
 *   the HTTP semantics perspective) and accepts a JSON body. GET requests
 *   cannot have a body and are unsuitable for CSRF-protected mutations.
 */
(function () {
  "use strict";

  const BADGE_CLASSES = {
    valid: "badge-valid",
    warning: "badge-warning",
    rejected: "badge-rejected",
  };

  /**
   * Sends a row dict to the AJAX endpoint and updates the table row's
   * status badge, issues column, and result indicator in-place.
   */
  async function validateRow(btn) {
    const row = btn.closest("tr");
    const resultSpan = btn.nextElementSibling;

    // Build payload from data-* attributes
    const payload = {
      asset_code: btn.dataset.assetCode || "",
      day_no: btn.dataset.dayNo || "",
      asset_type: btn.dataset.assetType || "",
      filename: btn.dataset.filename || "",
    };

    // Show spinner
    btn.disabled = true;
    resultSpan.innerHTML = '<span class="spinner"></span>';

    try {
      const response = await fetch(window.VALIDATE_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // Django requires X-CSRFToken header for AJAX POST requests
          "X-CSRFToken": window.CSRF_TOKEN,
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const text = await response.text();
        resultSpan.innerHTML = `<span style="color:#f87171">Error ${response.status}</span>`;
        console.error("Validation error:", text);
        return;
      }

      const data = await response.json();
      const status = data.status;
      const errors = data.errors || [];

      // Update status badge
      const badge = row.querySelector(".badge");
      if (badge) {
        badge.textContent = status;
        badge.className = "badge " + (BADGE_CLASSES[status] || "");
      }

      // Update issues column (6th td, index 6 = column "Issues")
      const issuesTd = row.cells[6];
      if (issuesTd) {
        if (errors.length > 0) {
          issuesTd.innerHTML =
            '<ul class="errors-list">' +
            errors.map((e) => `<li>${escapeHtml(e)}</li>`).join("") +
            "</ul>";
        } else {
          issuesTd.innerHTML = '<span style="color:#475569">—</span>';
        }
      }

      // Tick / Cross
      resultSpan.innerHTML =
        status === "valid"
          ? '<span style="color:#4ade80">✓</span>'
          : status === "warning"
          ? '<span style="color:#facc15">⚠</span>'
          : '<span style="color:#f87171">✗</span>';
    } catch (err) {
      resultSpan.innerHTML = '<span style="color:#f87171">Network error</span>';
      console.error("AJAX failed:", err);
    } finally {
      btn.disabled = false;
    }
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // Attach listeners once DOM is ready
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".btn-validate").forEach(function (btn) {
      btn.addEventListener("click", function () {
        validateRow(btn);
      });
    });
  });
})();
