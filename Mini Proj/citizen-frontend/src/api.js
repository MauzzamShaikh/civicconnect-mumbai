const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");
export const TOKEN_KEY = "bmc_citizen_access_token";

export async function apiRequest(path, { method = "GET", body, token } = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
  } catch {
    throw new Error("We couldn’t reach the service. Check your connection and try again.");
  }

  let data = {};
  let malformedResponse = false;
  try { data = await response.json(); } catch { malformedResponse = true; }
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : "";
    if (response.status === 400 && /verify-email|reset-password/.test(path)) {
      throw new Error("This link is invalid or has expired. Request a fresh email to continue.");
    }
    if (response.status === 413) throw new Error("This submission is too large. Choose a smaller image or video and try again.");
    if (response.status === 401 && path === "/auth/login") throw new Error("We couldn’t sign you in. Check your details and confirm your email is verified.");
    if (response.status === 401) throw new Error("Your session has expired. Please sign in again.");
    if (response.status === 403) throw new Error("This service is available to citizen accounts only.");
    if (response.status === 404 && path.startsWith("/citizen/track/")) throw new Error("No complaint was found for that reference.");
    if (response.status === 404) throw new Error("This complaint could not be found in your account.");
    if (response.status === 429) throw new Error("Too many attempts. Please wait a little and try again.");
    if (response.status === 422) throw new Error("Please check the description, file type, file size and map pin, then try again.");
    throw new Error("Something went wrong. Please try again in a moment.");
  }
  if (malformedResponse || data === null || typeof data !== "object") {
    throw new Error("The service returned an incomplete response. Please try again.");
  }
  return data;
}
