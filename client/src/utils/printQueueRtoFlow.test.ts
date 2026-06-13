import { beforeEach, describe, expect, it, vi } from "vitest";
import { runPrintQueueRtoFlow } from "./printQueueRtoFlow";

const insertRtoPayment = vi.fn();
const printGatePassLocal = vi.fn();
const pullSaleScanAssetsFromServer = vi.fn();
const pushSaleFolderToServer = vi.fn();

vi.mock("../electron", () => ({
  isElectron: vi.fn(() => false),
}));

vi.mock("../api/rtoPaymentDetails", () => ({
  insertRtoPayment: (...args: unknown[]) => insertRtoPayment(...args),
}));

vi.mock("../api/processFailureLog", () => ({
  recordPrintQueueRtoFailure: vi.fn(() => Promise.resolve()),
}));

vi.mock("../api/printRtoSidecar", () => ({
  printGatePassLocal: (...args: unknown[]) => printGatePassLocal(...args),
  pullSaleScanAssetsFromServer: (...args: unknown[]) => pullSaleScanAssetsFromServer(...args),
  pushSaleFolderToServer: (...args: unknown[]) => pushSaleFolderToServer(...args),
}));

vi.mock("../api/fillForms", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/fillForms")>();
  return {
    ...actual,
    overlayDealerSignaturesLocal: vi.fn(() => Promise.resolve()),
    finalizePrintRtoQueueLog: vi.fn(() => Promise.resolve()),
    dispatchPrintJobsFromApi: vi.fn(() => Promise.resolve({ ok: true, printed: 0 })),
  };
});

const baseInput = {
  dealerId: 100003,
  stagingId: "staging-uuid",
  subfolder: "7240275304_130626",
  customer: { name: "Devendra Singh", mobile_number: "7240275304" },
  vehicle: { model_name: "HF DELUXE" },
  vehicleId: 1,
};

describe("runPrintQueueRtoFlow browser dev", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    pullSaleScanAssetsFromServer.mockResolvedValue({
      success: true,
      files_downloaded: 0,
      files_failed: 0,
    });
    pushSaleFolderToServer.mockResolvedValue({
      success: true,
      files_uploaded: 0,
      files_failed: 0,
    });
    insertRtoPayment.mockResolvedValue({ ok: true, rto_queue_id: 42 });
  });

  it("does not block on Electron requirement", async () => {
    printGatePassLocal.mockResolvedValue({
      success: true,
      pdfs_saved: ["Gate Pass.pdf"],
      print_jobs: [],
    });

    const result = await runPrintQueueRtoFlow(baseInput);

    expect(result.success).toBe(true);
    expect(result.statusLines.some((l) => l.includes("requires the Electron app"))).toBe(false);
    expect(result.statusLines.some((l) => l.includes("Browser dev"))).toBe(true);
    expect(insertRtoPayment).toHaveBeenCalledWith({
      dealer_id: 100003,
      staging_id: "staging-uuid",
      status: "Queued",
    });
  });

  it("continues to RTO queue when gate pass fails in dev", async () => {
    printGatePassLocal.mockResolvedValue({
      success: false,
      pdfs_saved: [],
      error: "Missing PDFs in sale folder",
    });

    const result = await runPrintQueueRtoFlow(baseInput);

    expect(result.success).toBe(true);
    expect(result.gatePassSucceeded).toBe(false);
    expect(result.statusLines.some((l) => l.includes("dev, non-fatal"))).toBe(true);
    expect(result.statusLines.some((l) => l.includes("Added to RTO Queue"))).toBe(true);
    expect(insertRtoPayment).toHaveBeenCalledTimes(1);
  });
});

describe("runPrintQueueRtoFlow electron gate pass failure", () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    const electron = await import("../electron");
    vi.mocked(electron.isElectron).mockReturnValue(true);
    pullSaleScanAssetsFromServer.mockResolvedValue({
      success: true,
      files_downloaded: 0,
      files_failed: 0,
    });
    pushSaleFolderToServer.mockResolvedValue({
      success: true,
      files_uploaded: 0,
      files_failed: 0,
    });
    insertRtoPayment.mockResolvedValue({ ok: true, rto_queue_id: 42 });
  });

  it("aborts before queue insert when gate pass fails in Electron", async () => {
    printGatePassLocal.mockResolvedValue({
      success: false,
      pdfs_saved: [],
      error: "Missing PDFs in sale folder",
    });

    const result = await runPrintQueueRtoFlow(baseInput);

    expect(result.success).toBe(false);
    expect(result.gatePassSucceeded).toBe(false);
    expect(insertRtoPayment).not.toHaveBeenCalled();
  });
});
