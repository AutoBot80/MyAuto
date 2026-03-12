import { getBaseUrl } from "./client";

export interface QrDecodedEntry {
  raw: string;
  expanded?: string;
  parsed: Record<string, string | number | boolean>;
}

export interface QrDecodeResponse {
  decoded: QrDecodedEntry[];
  error: string | null;
}

const QR_DECODE_TIMEOUT_MS = 60_000;

export async function decodeQrFromImage(file: File): Promise<QrDecodeResponse> {
  const form = new FormData();
  form.append("file", file);
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), QR_DECODE_TIMEOUT_MS);
  try {
    const res = await fetch(`${getBaseUrl()}/qr-decode`, {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `QR decode failed (${res.status})`);
    }
    return res.json() as Promise<QrDecodeResponse>;
  } catch (err) {
    if (err instanceof Error) {
      if (err.name === "AbortError") {
        throw new Error("Request timed out. Try a smaller image (e.g. under 2 MB).");
      }
      throw err;
    }
    throw new Error("QR decode request failed");
  } finally {
    clearTimeout(timeoutId);
  }
}
