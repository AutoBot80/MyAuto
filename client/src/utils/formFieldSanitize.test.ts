import { describe, expect, it } from "vitest";
import { sanitizeFormFieldInputValue, sanitizeFormFieldValue } from "./formFieldSanitize";

describe("sanitizeFormFieldInputValue", () => {
  it("preserves a trailing space while typing multi-word values", () => {
    expect(sanitizeFormFieldInputValue("Ward ")).toBe("Ward ");
  });

  it("collapses internal whitespace runs but does not trim ends", () => {
    expect(sanitizeFormFieldInputValue("Near  Main ")).toBe("Near Main ");
  });

  it("stops at the first disallowed character", () => {
    expect(sanitizeFormFieldInputValue("Priya (Mother)")).toBe("Priya ");
  });
});

describe("sanitizeFormFieldValue", () => {
  it("trims and collapses whitespace for stored values", () => {
    expect(sanitizeFormFieldValue("Ward ")).toBe("Ward");
    expect(sanitizeFormFieldValue("Near  Main")).toBe("Near Main");
  });
});
