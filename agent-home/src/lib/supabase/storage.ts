/**
 * Server-side Supabase Storage for `agent-home` chat media (FG-20 Wave C1).
 *
 * The browser never holds a storage key: media is uploaded through the
 * `agent-home` BFF, which writes to a **principal-scoped** object path
 * (`<user_id>/<session>/<uuid>-<name>`) so one user's uploads can never collide
 * with or overwrite another's, and reads back a URL the chat thread renders.
 *
 * The feature degrades gracefully: when no storage key is configured on the
 * box (`storageConfigured()` is false) the upload route reports "not
 * configured" and the composer hides the attach affordance.
 */
import "server-only";

import { createClient } from "@supabase/supabase-js";

import {
  mediaBucket,
  storageConfigured,
  supabaseStorageKey,
  supabaseUrl,
} from "@/lib/env";
import type { ChatAttachment, Principal } from "@/types";

/** A safe object-path segment (no traversal, no separators). */
function slug(input: string): string {
  return (
    input
      .replace(/[^A-Za-z0-9._-]+/g, "_")
      // Collapse dot-runs so no segment can look like a `..` traversal.
      .replace(/\.{2,}/g, "_")
      .replace(/^[._]+|[._]+$/g, "") || "file"
  );
}

/**
 * Build the principal-scoped Storage object key
 * (`<user_id>/<session>/<uuid>-<name>`). Every segment is slugged so a crafted
 * user id, session id, or filename can never introduce `/` or `..` traversal
 * out of the principal's prefix.
 */
export function scopedMediaPath(
  principal: Principal,
  sessionId: string,
  fileName: string,
  unique: string,
): string {
  return `${slug(principal.user_id)}/${slug(sessionId || "new")}/${slug(unique)}-${slug(fileName)}`;
}

/**
 * Upload one file to principal-scoped Storage and return its reference. The
 * object key is prefixed with the principal's `user_id` so Storage-level
 * ownership matches the C1 principal. Throws when storage is not configured —
 * callers should check {@link storageAvailable} first.
 */
export async function uploadChatMedia(
  principal: Principal,
  sessionId: string,
  file: { name: string; contentType: string; bytes: ArrayBuffer },
): Promise<ChatAttachment> {
  const key = supabaseStorageKey();
  if (!key) {
    throw new Error("agent-home: Supabase Storage is not configured.");
  }
  const bucket = mediaBucket();
  const client = createClient(supabaseUrl(), key, {
    auth: { persistSession: false },
  });

  const unique =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}`;
  const path = scopedMediaPath(principal, sessionId, file.name, unique);

  const { error } = await client.storage
    .from(bucket)
    .upload(path, file.bytes, { contentType: file.contentType, upsert: false });
  if (error) {
    throw new Error(`agent-home: media upload failed — ${error.message}`);
  }

  const { data } = client.storage.from(bucket).getPublicUrl(path);
  return {
    path,
    url: data.publicUrl,
    name: file.name,
    content_type: file.contentType,
    size: file.bytes.byteLength,
  };
}

/** Whether the box is configured to accept chat-media uploads. */
export function storageAvailable(): boolean {
  return storageConfigured();
}
