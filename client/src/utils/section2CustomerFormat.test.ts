import { describe, expect, it } from "vitest";
import type { ExtractedCustomerDetails } from "../types";
import {
  buildAddressLine2,
  inProcessAddressFromStaging,
  normalizeOperatorFreeformAddress,
  uppercaseAddressField,
  uppercaseAddressLocality,
} from "./section2CustomerFormat";

describe("uppercaseAddressLocality", () => {
  it("uppercases lowercase locality clauses", () => {
    expect(uppercaseAddressLocality("ward 5, near post office")).toBe("WARD 5, NEAR POST OFFICE");
  });
});

describe("uppercaseAddressField", () => {
  it("uppercases a single address field", () => {
    expect(uppercaseAddressField("main road")).toBe("MAIN ROAD");
  });
});

describe("normalizeOperatorFreeformAddress", () => {
  it("uppercases city/state tail on New tab line 2", () => {
    const got = normalizeOperatorFreeformAddress("bharatpur, rajasthan, 321001", {
      minCommaSegments: 2,
    });
    expect(got).not.toBeNull();
    expect(got!.city).toBe("BHARATPUR");
    expect(got!.state).toBe("RAJASTHAN");
    expect(got!.address).toBe("BHARATPUR, RAJASTHAN, 321001");
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
