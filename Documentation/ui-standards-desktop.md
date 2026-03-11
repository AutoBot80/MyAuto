# UI Standards — Desktop (Dealership)

Most dealerships use **desktop** screens. These standards keep the client app readable and consistent.

---

## 1. Form size

| Setting        | Value        | Notes |
|----------------|--------------|--------|
| **Max form width** | **720px** | Fits 1366×768 and 1920×1080; avoids long lines. |
| **Min width**      | 320px   | Works on small or split windows. |
| **Form container** | Centered, `max-width: 720px`, padding 24–32px. |

Common desktop resolutions: **1920×1080**, **1366×768**, **1280×720**. Forms are not full-width so content stays scannable.

---

## 2. Font family

| Use        | Font stack |
|-----------|------------|
| **Primary** | **Segoe UI**, system-ui, -apple-system, **Arial**, sans-serif |

- **Segoe UI** — common on Windows desktops (dealerships).
- **system-ui / -apple-system** — native UI font on Mac/Linux.
- **Arial** — safe fallback everywhere.

Avoid decorative or thin fonts; stick to one clear sans-serif.

---

## 3. Font sizes

| Element       | Size   | Use |
|---------------|--------|-----|
| **Body / form text** | **14px** | Default content. |
| **Labels**    | **13px** | Slightly smaller, can be muted. |
| **Inputs**    | **15px** | Reduces pinch-zoom on focus (accessibility). |
| **Buttons**   | **14px** | Matches body. |
| **H1 (page)** | **22px** | Main page title. |
| **H2 (section)** | **18px** | Section heading. |
| **H3**        | **16px** | Subsection. |
| **Small / hint** | **12px** | Helper text. |

Line height: **1.5** for body, **1.25** for headings.

---

## 4. Summary

- **Form width:** max **720px**, centered.
- **Font:** **Segoe UI**, system-ui, Arial, sans-serif.
- **Body:** **14px**; **inputs 15px**; **labels 13px**; **H1 22px**, **H2 18px**.

These values are applied in the client’s global and app CSS.
