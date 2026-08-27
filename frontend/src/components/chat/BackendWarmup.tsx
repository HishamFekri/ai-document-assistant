"use client";

import {
  useEffect,
} from "react";


export default function BackendWarmup() {
  useEffect(() => {
    const apiUrl =
      process.env.NEXT_PUBLIC_API_URL
      ?? "http://localhost:8000";

    const controller =
      new AbortController();

    fetch(
      `${apiUrl}/health`,
      {
        method: "GET",

        credentials:
          "include",

        cache:
          "no-store",

        signal:
          controller.signal,
      }
    ).catch(() => {
      // Warm-up only.
      // Login must not fail if this request fails.
    });

    return () => {
      controller.abort();
    };
  }, []);

  return null;
}