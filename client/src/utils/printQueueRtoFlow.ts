/**
 * Shared Print / Queue RTO orchestration (New tab + In-process).
 * Order: pull scans → overlay → local gate pass → print → scanner archive → log → push → RTO queue.
 */
import {
  dispatchPrintJobsFromApi,
  finalizePrintRtoQueueLog,
  overlayDealerSignaturesLocal,
  printRtoQueueLogHint,
  type FillDmsCustomer,
  type PrintForm20Response,
  type PrintRtoQueueLogLine,
} from "../api/fillForms";
import { insertRtoPayment } from "../api/rtoPaymentDetails";
import { isElectron } from "../electron";
import {
  printGatePassLocal,
  pullSaleScanAssetsFromServer,
  pushSaleFolderToServer,
} from "../api/printRtoSidecar";
import {
  moveConsolidatedToProcessed,
  type ConsolidatedFsArchiveContext,
} from "./scannerArchive";

export interface RunPrintQueueRtoFlowInput {
  dealerId: number;
  stagingId: string;
  subfolder: string;
  customer: FillDmsCustomer;
  vehicle: Record<string, unknown>;
  vehicleId?: number | null;
  pendingScannerArchiveMove?: ConsolidatedFsArchiveContext | null;
}

export interface RunPrintQueueRtoFlowResult {
  success: boolean;
  statusLines: string[];
  gatePassSucceeded: boolean;
  error?: string;
}

export async function runPrintQueueRtoFlow(
  input: RunPrintQueueRtoFlowInput
): Promise<RunPrintQueueRtoFlowResult> {
  const statusLines: string[] = [];
  const traceLines: PrintRtoQueueLogLine[] = [];
  const dealerId = input.dealerId;
  const subfolder = (input.subfolder || "").trim();
  const stagingId = (input.stagingId || "").trim();

  if (!subfolder || dealerId <= 0) {
    return {
      success: false,
      statusLines: ["dealer_id and subfolder are required."],
      gatePassSucceeded: false,
      error: "dealer_id and subfolder are required.",
    };
  }

  if (!isElectron()) {
    return {
      success: false,
      statusLines: ["Print / Queue RTO requires the Electron app."],
      gatePassSucceeded: false,
      error: "Electron required",
    };
  }

  const pull = await pullSaleScanAssetsFromServer({ dealer_id: dealerId, subfolder });
  if (!pull.success) {
    traceLines.push({ prefix: "UI", message: `pull FAIL: ${pull.error ?? "unknown"}` });
    await finalizePrintRtoQueueLog({ dealer_id: dealerId, subfolder, lines: traceLines });
    const err = pull.error ?? "Download sale folder from server failed.";
    return {
      success: false,
      statusLines: [`${err} ${printRtoQueueLogHint(subfolder)}`],
      gatePassSucceeded: false,
      error: err,
    };
  }
  if ((pull.files_downloaded ?? 0) > 0) {
    statusLines.push(`Downloaded ${pull.files_downloaded} file(s) from server sale folder.`);
  }
  traceLines.push({
    prefix: "UI",
    message: `pull OK downloaded=${pull.files_downloaded ?? 0} failed=${pull.files_failed ?? 0}`,
  });

  try {
    await overlayDealerSignaturesLocal({ dealerId, subfolder });
    traceLines.push({ prefix: "UI", message: "dealer signature overlay finished (best-effort)" });
  } catch (overlayErr) {
    traceLines.push({
      prefix: "UI",
      message: `dealer signature overlay error: ${overlayErr instanceof Error ? overlayErr.message : String(overlayErr)}`,
    });
  }

  let gatePassRes: PrintForm20Response;
  try {
    gatePassRes = await printGatePassLocal({
      subfolder,
      customer: input.customer,
      vehicle: input.vehicle,
      vehicle_id: input.vehicleId ?? undefined,
      dealer_id: dealerId,
      staging_id: stagingId || undefined,
    });
  } catch (printErr) {
    const msg = printErr instanceof Error ? printErr.message : "Generate Gate Pass failed.";
    traceLines.push({ prefix: "UI", message: `gate pass exception: ${msg}` });
    await finalizePrintRtoQueueLog({ dealer_id: dealerId, subfolder, lines: traceLines });
    return {
      success: false,
      statusLines: [`Gate Pass: ${msg} ${printRtoQueueLogHint(subfolder)}`],
      gatePassSucceeded: false,
      error: msg,
    };
  }

  const gatePassSucceeded = !!gatePassRes.success;
  if (!gatePassSucceeded) {
    const err = gatePassRes.error ?? "Gate Pass generation failed.";
    traceLines.push({ prefix: "UI", message: `gate pass FAIL: ${err}` });
    await finalizePrintRtoQueueLog({ dealer_id: dealerId, subfolder, lines: traceLines });
    return {
      success: false,
      statusLines: [`Gate Pass: ${err} ${printRtoQueueLogHint(subfolder)}`],
      gatePassSucceeded: false,
      error: err,
    };
  }

  const printResult = await dispatchPrintJobsFromApi(gatePassRes.print_jobs);
  statusLines.push(`Gate Pass saved: ${(gatePassRes.pdfs_saved ?? []).join(", ")}`);
  if (printResult.ok) {
    statusLines.push(
      `Sent ${printResult.printed} document(s) to the printer (Sale Certificate, Insurance, Gate Pass).`
    );
    traceLines.push({
      prefix: "UI",
      message: `print OK printed=${printResult.printed} jobs=${(gatePassRes.print_jobs ?? []).map((j) => j.filename).join(", ")}`,
    });
  } else {
    const printErr = printResult.error ?? "Print failed.";
    statusLines.push(`Print: ${printErr}`);
    traceLines.push({ prefix: "UI", message: `print FAIL: ${printErr}` });
  }

  if (input.pendingScannerArchiveMove) {
    const arch = input.pendingScannerArchiveMove;
    try {
      await moveConsolidatedToProcessed(arch.fileHandles, arch.scannerRoot);
      statusLines.push("Moved scan from landing to processed folder.");
      traceLines.push({ prefix: "UI", message: "scanner archive move OK" });
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      statusLines.push(`Could not move file to processed: ${detail}`);
      traceLines.push({ prefix: "UI", message: `scanner archive move FAIL: ${detail}` });
    }
  }

  traceLines.push({ prefix: "UI", message: "uploading trace log" });
  await finalizePrintRtoQueueLog({ dealer_id: dealerId, subfolder, lines: traceLines });

  const push = await pushSaleFolderToServer({ dealer_id: dealerId, subfolder });
  if (!push.success) {
    const err = push.error ?? "Upload sale folder to server failed.";
    await finalizePrintRtoQueueLog({
      dealer_id: dealerId,
      subfolder,
      lines: [{ prefix: "UI", message: `push FAIL: ${err}` }],
    });
    return {
      success: false,
      statusLines: [
        ...statusLines,
        `${err} ${printRtoQueueLogHint(subfolder)}`,
      ],
      gatePassSucceeded: true,
      error: err,
    };
  }
  const pushParts: string[] = [];
  if ((push.files_uploaded ?? 0) > 0) pushParts.push(`${push.files_uploaded} to server`);
  if (pushParts.length > 0) statusLines.push(`Uploaded ${pushParts.join(", ")}.`);

  if (!stagingId) {
    return {
      success: false,
      statusLines: [
        ...statusLines,
        "RTO queue: staging_id missing.",
        printRtoQueueLogHint(subfolder),
      ],
      gatePassSucceeded: true,
      error: "staging_id missing",
    };
  }

  try {
    await insertRtoPayment({
      dealer_id: dealerId,
      staging_id: stagingId,
      status: "Queued",
    });
    statusLines.push("Added to RTO Queue.");
    await finalizePrintRtoQueueLog({
      dealer_id: dealerId,
      subfolder,
      lines: [{ prefix: "UI", message: "RTO queue insert OK" }],
    });
  } catch (queueErr) {
    const msg =
      queueErr instanceof Error ? `RTO queue: ${queueErr.message}` : "RTO queue insert failed.";
    await finalizePrintRtoQueueLog({
      dealer_id: dealerId,
      subfolder,
      lines: [{ prefix: "UI", message: msg }],
    });
    return {
      success: false,
      statusLines: [...statusLines, `${msg} ${printRtoQueueLogHint(subfolder)}`],
      gatePassSucceeded: true,
      error: msg,
    };
  }

  statusLines.push(printRtoQueueLogHint(subfolder));
  return {
    success: true,
    statusLines,
    gatePassSucceeded: true,
  };
}
