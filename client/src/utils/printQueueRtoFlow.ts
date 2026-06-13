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
import { recordPrintQueueRtoFailure } from "../api/processFailureLog";
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

async function logPrintQueueRtoFailure(
  input: RunPrintQueueRtoFlowInput,
  errorText: string,
  rtoQueueId?: number | null
): Promise<void> {
  await recordPrintQueueRtoFailure({
    dealerId: input.dealerId,
    subfolder: input.subfolder,
    customer: input.customer,
    errorText,
    rtoQueueId,
  });
}

export async function runPrintQueueRtoFlow(
  input: RunPrintQueueRtoFlowInput
): Promise<RunPrintQueueRtoFlowResult> {
  const statusLines: string[] = [];
  const traceLines: PrintRtoQueueLogLine[] = [];
  const dealerId = input.dealerId;
  const subfolder = (input.subfolder || "").trim();
  const stagingId = (input.stagingId || "").trim();
  const failureLog = { dealerId, subfolder, customer: input.customer };

  if (!subfolder || dealerId <= 0) {
    return {
      success: false,
      statusLines: ["dealer_id and subfolder are required."],
      gatePassSucceeded: false,
      error: "dealer_id and subfolder are required.",
    };
  }

  const browserDev = !isElectron();
  const devNonFatalGatePass = browserDev && import.meta.env.DEV;
  if (browserDev) {
    statusLines.push("Browser dev: skipping local print/pull/push; gate pass via API.");
  }

  const pull = await pullSaleScanAssetsFromServer({ dealer_id: dealerId, subfolder });
  if (!pull.success) {
    traceLines.push({ prefix: "UI", message: `pull FAIL: ${pull.error ?? "unknown"}` });
    await finalizePrintRtoQueueLog({ dealer_id: dealerId, subfolder, lines: traceLines });
    const err = pull.error ?? "Download sale folder from server failed.";
    await logPrintQueueRtoFailure(input, `Pull: ${err}`);
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

  let gatePassRes: PrintForm20Response | null = null;
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
    if (devNonFatalGatePass) {
      statusLines.push(`Gate Pass (dev, non-fatal): ${msg}`);
      traceLines.push({ prefix: "UI", message: `gate pass dev-continue after exception: ${msg}` });
    } else {
      await finalizePrintRtoQueueLog({ dealer_id: dealerId, subfolder, lines: traceLines });
      await logPrintQueueRtoFailure(input, `Gate pass: ${msg}`);
      return {
        success: false,
        statusLines: [`Gate Pass: ${msg} ${printRtoQueueLogHint(subfolder)}`],
        gatePassSucceeded: false,
        error: msg,
      };
    }
  }

  let gatePassSucceeded = !!gatePassRes?.success;
  if (!gatePassSucceeded) {
    const err = gatePassRes?.error ?? "Gate Pass generation failed.";
    traceLines.push({ prefix: "UI", message: `gate pass FAIL: ${err}` });
    if (devNonFatalGatePass) {
      statusLines.push(`Gate Pass (dev, non-fatal): ${err}`);
      traceLines.push({ prefix: "UI", message: `gate pass dev-continue: ${err}` });
      gatePassSucceeded = false;
    } else {
      await finalizePrintRtoQueueLog({ dealer_id: dealerId, subfolder, lines: traceLines });
      await logPrintQueueRtoFailure(input, `Gate pass: ${err}`);
      return {
        success: false,
        statusLines: [`Gate Pass: ${err} ${printRtoQueueLogHint(subfolder)}`],
        gatePassSucceeded: false,
        error: err,
      };
    }
  } else if (gatePassRes) {
    statusLines.push(`Gate Pass saved: ${(gatePassRes.pdfs_saved ?? []).join(", ")}`);
    const jobNames = (gatePassRes.print_jobs ?? []).map((j) => j.filename).join(", ");
    if (gatePassRes.print_jobs?.length) {
      void dispatchPrintJobsFromApi(gatePassRes.print_jobs, { failureLog });
      statusLines.push(
        `Printing ${gatePassRes.print_jobs.length} document(s) in the background (Sale Certificate, Insurance, Gate Pass).`
      );
      traceLines.push({
        prefix: "UI",
        message: `print started (background) jobs=${jobNames}`,
      });
    }
  }

  traceLines.push({ prefix: "UI", message: "uploading trace log (pre-push)" });
  await finalizePrintRtoQueueLog({ dealer_id: dealerId, subfolder, lines: traceLines });
  traceLines.length = 0;

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

  const push = await pushSaleFolderToServer({ dealer_id: dealerId, subfolder });
  if (!push.success) {
    const err = push.error ?? "Upload sale folder to server failed.";
    await finalizePrintRtoQueueLog({
      dealer_id: dealerId,
      subfolder,
      lines: [{ prefix: "UI", message: `push FAIL: ${err}` }],
    });
    await logPrintQueueRtoFailure(input, `Push: ${err}`);
    return {
      success: false,
      statusLines: [
        ...statusLines,
        `${err} ${printRtoQueueLogHint(subfolder)}`,
      ],
      gatePassSucceeded,
      error: err,
    };
  }
  const pushParts: string[] = [];
  if ((push.files_uploaded ?? 0) > 0) pushParts.push(`${push.files_uploaded} to server`);
  if (pushParts.length > 0) statusLines.push(`Uploaded ${pushParts.join(", ")}.`);
  traceLines.push({
    prefix: "UI",
    message: `push OK uploaded=${push.files_uploaded ?? 0} failed=${push.files_failed ?? 0}`,
  });
  if ((push.files_failed ?? 0) > 0) {
    await logPrintQueueRtoFailure(
      input,
      `Push: ${push.files_failed} file(s) failed to upload (${push.files_uploaded ?? 0} ok). See Print_RTO_queue.txt PUSH lines.`
    );
  }

  if (!stagingId) {
    await finalizePrintRtoQueueLog({ dealer_id: dealerId, subfolder, lines: traceLines });
    await logPrintQueueRtoFailure(input, "RTO queue: staging_id missing.");
    return {
      success: false,
      statusLines: [
        ...statusLines,
        "RTO queue: staging_id missing.",
        printRtoQueueLogHint(subfolder),
      ],
      gatePassSucceeded,
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
    await logPrintQueueRtoFailure(input, msg);
    return {
      success: false,
      statusLines: [...statusLines, `${msg} ${printRtoQueueLogHint(subfolder)}`],
      gatePassSucceeded,
      error: msg,
    };
  }

  statusLines.push(printRtoQueueLogHint(subfolder));
  await finalizePrintRtoQueueLog({ dealer_id: dealerId, subfolder, lines: traceLines });
  return {
    success: true,
    statusLines,
    gatePassSucceeded,
  };
}
