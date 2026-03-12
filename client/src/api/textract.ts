import { getBaseUrl } from "./client";

export interface TextractResponse {
  full_text: string;
  blocks: { BlockType?: string; Text?: string; Confidence?: number }[];
  raw_response: { BlockCount?: number; DocumentMetadata?: unknown } | null;
  error: string | null;
}

export async function extractWithTextract(file: File): Promise<TextractResponse> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${getBaseUrl()}/textract/extract`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Textract failed (${res.status})`);
  }
  return res.json() as Promise<TextractResponse>;
}
