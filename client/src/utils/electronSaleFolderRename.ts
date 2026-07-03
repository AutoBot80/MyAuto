import { isElectron } from "../electron";

/** Mirror server folder leaf rename on dealer PC (Uploaded scans + ocr_output). */
export async function renameSaleSubfoldersOnDealerPc(opts: {
  dealerId: number;
  oldSubfolder: string;
  newSubfolder: string;
}): Promise<void> {
  if (!isElectron() || !window.electronAPI?.file?.renameSaleSubfolders) {
    return;
  }
  const oldSub = opts.oldSubfolder.trim();
  const newSub = opts.newSubfolder.trim();
  if (!oldSub || !newSub || oldSub === newSub) {
    return;
  }
  const result = await window.electronAPI.file.renameSaleSubfolders({
    dealerId: opts.dealerId,
    oldSubfolder: oldSub,
    newSubfolder: newSub,
  });
  if (!result.ok) {
    throw new Error(result.message || "Could not rename sale folders on this PC.");
  }
}
