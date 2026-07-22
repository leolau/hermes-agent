/**
 * POST /api/chat/upload — BFF chat-media upload (FG-20 Wave C1).
 *
 * Accepts a `multipart/form-data` file, uploads it to principal-scoped Supabase
 * Storage server-side (the browser never holds the storage key), and returns
 * the attachment reference the composer attaches to the outgoing message.
 * Responds 501 when Storage is not configured on the box.
 */
import { NextResponse } from "next/server";

import { getPrincipal } from "@/lib/auth/principal";
import { storageAvailable, uploadChatMedia } from "@/lib/supabase/storage";

const MAX_BYTES = 10 * 1024 * 1024;

export async function POST(request: Request): Promise<NextResponse> {
  const principal = await getPrincipal();
  if (!principal) {
    return NextResponse.json({ error: "unauthenticated" }, { status: 401 });
  }
  if (!storageAvailable()) {
    return NextResponse.json(
      { error: "storage_unconfigured", detail: "Media storage is not configured." },
      { status: 501 },
    );
  }

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json({ error: "invalid_form" }, { status: 400 });
  }
  const sessionId = (form.get("sessionId") as string | null) ?? "";
  const file = form.get("file");
  if (!(file instanceof File)) {
    return NextResponse.json(
      { error: "missing_file", detail: "A file field is required." },
      { status: 400 },
    );
  }
  if (file.size > MAX_BYTES) {
    return NextResponse.json(
      { error: "too_large", detail: "File exceeds the 10 MB limit." },
      { status: 413 },
    );
  }

  try {
    const attachment = await uploadChatMedia(principal, sessionId, {
      name: file.name || "upload",
      contentType: file.type || "application/octet-stream",
      bytes: await file.arrayBuffer(),
    });
    return NextResponse.json(attachment);
  } catch (err) {
    return NextResponse.json(
      {
        error: "upload_failed",
        detail: err instanceof Error ? err.message : "Upload failed.",
      },
      { status: 502 },
    );
  }
}
