import { createContext, useContext } from "react";

export function isBetaMode(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return new URLSearchParams(window.location.search).get("beta") === "true";
  } catch {
    return false;
  }
}

export const BetaContext = createContext<boolean>(false);

export function useBeta(): boolean {
  return useContext(BetaContext);
}
