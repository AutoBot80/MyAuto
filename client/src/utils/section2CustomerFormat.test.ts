import { describe, expect, it } from "vitest";
import type { ExtractedCustomerDetails } from "../types";
import {
  buildAddressLine1,
  buildAddressLine2,
  inProcessAddressFromStaging,
  normalizeOperatorFreeformAddress,
  stripAddressLine2Suffix,
  uppercaseAddressField,
  uppercaseAddressLocality,
  uppercaseCareOf,
} from "./section2CustomerFormat";

describe("uppercaseCareOf", () => {
  it("uppercases relation prefix and name", () => {
    expect(uppercaseCareOf("s/o ram singh")).toBe("S/O RAM SINGH");
  });
});

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

describe("stripAddressLine2Suffix", () => {
  const tailCols: ExtractedCustomerDetails = {
    city: "Bharatpur",
    state: "Rajasthan",
    pin_code: "321001",
  };

  it("removes matching city/state/pin tail from full address", () => {
    expect(stripAddressLine2Suffix("Ward 5, Bharatpur, Rajasthan, 321001", tailCols)).toBe("Ward 5");
  });

  it("removes State - PIN dash tail variant", () => {
    expect(stripAddressLine2Suffix("Ward 5, Bharatpur, Rajasthan - 321001", tailCols)).toBe(
      "Ward 5"
    );
  });

  it("leaves locality-only address unchanged", () => {
    expect(stripAddressLine2Suffix("Ward 5, Main Road", tailCols)).toBe("Ward 5, Main Road");
  });
});

describe("buildAddressLine1", () => {
  it("strips duplicated city/state/pin tail when columns are separate", () => {
    const c: ExtractedCustomerDetails = {
      address: "Ward 5, Bharatpur, Rajasthan, 321001",
      city: "Bharatpur",
      state: "Rajasthan",
      pin_code: "321001",
    };
    expect(buildAddressLine1(c)).toBe("Ward 5");
  });

  it("keeps locality-only address when columns are separate", () => {
    const c: ExtractedCustomerDetails = {
      address: "Ward 5, Main Road",
      city: "Bharatpur",
      state: "Rajasthan",
      pin_code: "321001",
    };
    expect(buildAddressLine1(c)).toBe("Ward 5, Main Road");
  });

  it("prefers granular fields over address fallback", () => {
    const c: ExtractedCustomerDetails = {
      house: "12",
      street: "Main Road",
      address: "Ward 5, Bharatpur, Rajasthan, 321001",
      city: "Bharatpur",
      state: "Rajasthan",
      pin_code: "321001",
    };
    expect(buildAddressLine1(c)).toBe("12, Main Road");
  });

  it("strips dash-before-PIN tail variant", () => {
    const c: ExtractedCustomerDetails = {
      address: "Ward 5, Bharatpur, Rajasthan - 321001",
      city: "Bharatpur",
      state: "Rajasthan",
      pin_code: "321001",
    };
    expect(buildAddressLine1(c)).toBe("Ward 5");
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
