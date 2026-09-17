const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const LOGOUT_ERROR = "Could not confirm sign out. You may still be signed in. Please try again.";

export async function logoutSession(onSuccess: () => void, onFailure: (message: string) => void) {
  try {
    const response = await fetch(`${API_URL}/auth/logout`, {
      method: "POST",
      credentials: "include",
    });
    if (!response.ok) throw new Error(LOGOUT_ERROR);
  } catch {
    onFailure(LOGOUT_ERROR);
    return;
  }
  onSuccess();
}
