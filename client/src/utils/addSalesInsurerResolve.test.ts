import { describe, expect, it } from "vitest";
import { mapInsurance } from "../api/submitInfo";
import { insurerLooksLikeFinancier, resolvePortalInsurer } from "./addSalesInsurerResolve";

const PORTAL = [
  "National Insurance Co. Ltd.",
  "The New India Assurance Co. Ltd.",
  "BAJAJ GENERAL INSURANCE LIMITED",
] as const;

const FINANCERS = ["Bajaj Finance", "Bajaj Auto Finance", "Shriram Finance Ltd."] as const;

describe("resolvePortalInsurer", () => {
  it("prefers portal OCR over dealer default when OCR is valid", () => {
    expect(
      resolvePortalInsurer("The New India Assurance Co. Ltd.", "National Insurance Co. Ltd.", PORTAL)
    ).toBe("The New India Assurance Co. Ltd.");
  });

  it("falls back to dealer prefer when OCR is garbage", () => {
    expect(resolvePortalInsurer("Dajaj Finate", "The New India Assurance Co. Ltd.", PORTAL)).toBe(
      "The New India Assurance Co. Ltd."
    );
  });

  it("returns undefined when neither OCR nor prefer is in portal list", () => {
    expect(resolvePortalInsurer("Dajaj Finate", "HDFC ERGO General Insurance", PORTAL)).toBeUndefined();
  });
});

describe("insurerLooksLikeFinancier", () => {
  it("detects OCR financier bleed", () => {
    expect(insurerLooksLikeFinancier("Dajaj Finate", FINANCERS)).toBe(true);
  });
});

describe("mapInsurance", () => {
  it("uses prefer_insurer when extracted insurer is not portal-eligible", () => {
    const out = mapInsurance(
      { insurer: "Dajaj Finate" },
      "The New India Assurance Co. Ltd.",
      PORTAL
    );
    expect(out.insurer).toBe("The New India Assurance Co. Ltd.");
  });
});
