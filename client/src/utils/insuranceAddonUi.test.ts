import { describe, expect, it } from "vitest";
import {
  insuranceAddonDisplayLabel,
  mergeSelectedInsuranceAddonOption,
  normalizeInsuranceAddonRows,
} from "./insuranceAddonUi";

describe("insuranceAddonUi", () => {
  it("normalizes addon rows", () => {
    expect(
      normalizeInsuranceAddonRows([
        { insurance_addon_id: 2, display_label: "ND Cover, Rim Safeguard" },
        { insurance_addon_id: 0, display_label: "bad" },
      ])
    ).toEqual([{ insurance_addon_id: 2, display_label: "ND Cover, Rim Safeguard" }]);
  });

  it("merges selected id with label from catalog lookup", () => {
    const options = [{ insurance_addon_id: 1, display_label: "ND Cover, Rim Safeguard, RSA" }];
    const catalog = [
      { insurance_addon_id: 6, display_label: "ND Cover" },
      ...options,
    ];
    expect(mergeSelectedInsuranceAddonOption(options, 6, catalog)).toEqual([
      { insurance_addon_id: 6, display_label: "ND Cover" },
      { insurance_addon_id: 1, display_label: "ND Cover, Rim Safeguard, RSA" },
    ]);
  });

  it("displays label not raw id", () => {
    const rows = [{ insurance_addon_id: 2, display_label: "ND Cover, Rim Safeguard" }];
    expect(insuranceAddonDisplayLabel(2, rows)).toBe("ND Cover, Rim Safeguard");
    expect(insuranceAddonDisplayLabel(99, rows)).toBe("—");
  });
});
