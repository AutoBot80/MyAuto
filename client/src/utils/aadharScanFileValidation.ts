/** Matches insurer KYC upload rules (jpg/jpeg/png/img, max 512 KB). */
export const AADHAR_SCAN_MAX_BYTES = 512 * 1024;

const ALLOWED_EXTENSIONS = new Set(["jpg", "jpeg", "png", "img"]);

function fileExtensionLower(name: string): string {
  const i = name.lastIndexOf(".");
  if (i < 0) return "";
  return name.slice(i + 1).toLowerCase();
}

/**
 * Returns an error message if the file is not allowed for Aadhaar front/back uploads, else null.
 */
export function validateAadharScanFile(file: File): string | null {
  const ext = fileExtensionLower(file.name);
  if (!ext || !ALLOWED_EXTENSIONS.has(ext)) {
    return "Aadhaar files must be jpg, jpeg, png, or img only.";
  }
  if (file.size > AADHAR_SCAN_MAX_BYTES) {
    return "Each Aadhaar file must be at most 512 KB.";
  }
  return null;
}
