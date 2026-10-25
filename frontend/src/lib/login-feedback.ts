// Fixed, user-facing messages for the backend's ?google_login=<reason> redirects.
// Unknown values are ignored: the URL value itself is never displayed.

export const GOOGLE_LOGIN_MESSAGES: Record<string, string> = {
  cancelled: "Google sign-in was cancelled.",
  expired: "Your sign-in attempt expired. Please try again.",
  failed: "We couldn't sign you in with Google. Please try again.",
  unavailable: "Google sign-in is temporarily unavailable. Please try again shortly.",
};

export function googleLoginMessage(search: string): string | null {
  const reason = new URLSearchParams(search).get("google_login");
  return reason !== null && Object.prototype.hasOwnProperty.call(GOOGLE_LOGIN_MESSAGES, reason)
    ? GOOGLE_LOGIN_MESSAGES[reason]
    : null;
}

export function withoutGoogleLogin(href: string): string {
  const url = new URL(href);
  url.searchParams.delete("google_login");
  return `${url.pathname}${url.search}${url.hash}`;
}
