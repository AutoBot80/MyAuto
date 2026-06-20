/**
 * View Customer **Print File**: pull sale PDFs, overlay signatures, merge Form 20 + Form 22 + GST, print.
 * Uses the same Electron print path as Print / Queue RTO (silent print preference, dialog assist).
 */
import { dispatchPrintJobsFromApi, type FillDmsCustomer } from "../api/fillForms";
import {
  overlayDealerSignaturesLocal,
  printViewCustomerSaleFilesLocal,
  pullSaleScanAssetsFromServer,
} from "../api/printRtoSidecar";
import { isElectron } from "../electron";

export interface RunViewCustomerPrintFilesInput {
  dealerId: number;
  subfolder: string;
  customer: FillDmsCustomer;
}

export interface RunViewCustomerPrintFilesResult {
  success: boolean;
  message: string;
}

export async function runViewCustomerPrintFilesFlow(
  input: RunViewCustomerPrintFilesInput
): Promise<RunViewCustomerPrintFilesResult> {
  const subfolder = (input.subfolder || "").trim();
  const dealerId = input.dealerId;

  if (!subfolder || dealerId <= 0) {
    return { success: false, message: "Select a vehicle with a document folder first." };
  }
  if (!isElectron()) {
    return {
      success: false,
      message: "Print File requires the Saathi desktop app (Electron).",
    };
  }

  const pull = await pullSaleScanAssetsFromServer({ dealer_id: dealerId, subfolder });
  if (!pull.success) {
    return {
      success: false,
      message: pull.error ?? "Could not download sale folder from the server.",
    };
  }

  try {
    await overlayDealerSignaturesLocal({ dealer_id: dealerId, subfolder });
  } catch {
    /* best-effort, same as Print / Queue RTO */
  }

  const mobile =
    (input.customer.mobile_number ?? input.customer.mobile ?? "").trim() || undefined;
  const build = await printViewCustomerSaleFilesLocal({
    dealer_id: dealerId,
    subfolder,
    mobile,
    customer: input.customer,
  });
  if (!build.success) {
    return {
      success: false,
      message: build.error ?? "Could not prepare Form 20, Form 22, and GST invoices for printing.",
    };
  }

  const jobs = build.print_jobs ?? [];
  if (!jobs.length) {
    return { success: false, message: "No print jobs were returned." };
  }

  const printed = await dispatchPrintJobsFromApi(jobs, { awaitCompletion: true });
  if (!printed.ok) {
    return {
      success: false,
      message: printed.error ?? "Printing failed.",
    };
  }

  return {
    success: true,
    message: `Printing Form 20, Form 22, and GST Retail Invoice (${printed.printed} job).`,
  };
}
