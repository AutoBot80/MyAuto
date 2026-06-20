import { describe, expect, it } from "vitest";
import type { ExtractedCustomerDetails } from "../types";
import {
  buildAddressLine2,
  inProcessAddressFromStaging,
  initCapWords,
  titleCaseAddressLocality,
} from "./section2CustomerFormat";

describe("titleCaseAddressLocality", () => {
  it("init-caps lowercase locality clauses", () => {
    expect(titleCaseAddressLocality("ward 5, near post office")).toBe("Ward 5, Near Post Office");
  });
});

describe("initCapWords", () => {
  it("init-caps a single locality field", () => {
    expect(initCapWords("main road")).toBe("Main Road");
  });
});

describe("inProcessAddressFromStaging", () => {
  it("combines New Sales line 1 address with city/state/pin columns", () => {
    const c: ExtractedCustomerDetails = {
      address: "Ward 5, Main Road",
      city: "Bharatpur",
      state: "Rajasthan",
      pin_code: "321001",
    };
    expect(inProcessAddressFromStaging(c)).toBe(
      "Ward 5, Main Road, Bharatpur, Rajasthan, 321001"
    );
  });

  it("returns full operator line without duplicating city/state/pin tail", () => {
    const c: ExtractedCustomerDetails = {
      address: "Ward 5, Bharatpur, Rajasthan, 321001",
      city: "Bharatpur",
      state: "Rajasthan",
      pin_code: "321001",
    };
    expect(inProcessAddressFromStaging(c)).toBe("Ward 5, Bharatpur, Rajasthan, 321001");
  });

  it("returns line 2 only when address is empty", () => {
    const c: ExtractedCustomerDetails = {
      city: "Bharatpur",
      state: "Rajasthan",
      pin_code: "321001",
    };
    expect(inProcessAddressFromStaging(c)).toBe(buildAddressLine2(c));
  });
});
