"use client";

import { useEffect, useState } from "react";
import { getUploadPolicy } from "@/lib/chat-api";
import type { UploadPolicy } from "@/lib/upload-policy";

export function useUploadPolicy() {
  const [policy, setPolicy] = useState<UploadPolicy | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    getUploadPolicy("__cookie__").then(
      (value) => { if (active) setPolicy(value); },
      () => { if (active) setError(true); },
    );
    return () => { active = false; };
  }, []);
  return { policy, error };
}
