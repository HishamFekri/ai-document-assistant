"use client";
import { useLayoutEffect, useMemo } from "react";
import { RequestScope } from "@/lib/request-scope";

export function useRequestScope(key: string) {
  const scope = useMemo(() => new RequestScope(key), [key]);
  useLayoutEffect(() => {
    scope.activate();
    return () => scope.dispose();
  }, [scope]);
  return scope;
}
