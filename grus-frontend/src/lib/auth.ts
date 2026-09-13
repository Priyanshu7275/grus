// Cognito three-step gate isn't wired up yet (the backend brief says so
// explicitly: "build against the open API and we add the token layer
// later"). This mirrors Sammy's cosmetic gate shape so it's a drop-in
// swap for real Cognito calls later — same three functions, same demo
// credentials pattern.

const KEY = "grus_session";

export const DEMO_CREDENTIALS = {
  hospitalCode: "GRUS-ED-4471",
  email: "clinician@hospital-x.org",
  password: "grus-demo",
  otp: "424242",
};

export function isLoggedIn(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(KEY) === "1";
}

export function login() {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(KEY, "1");
}

export function logout() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(KEY);
}

// Cosmetic verification — accepts anything non-empty (or the demo
// credentials, which work as a documented shortcut). Swap the body of
// these three for real Cognito calls when the token layer lands.
async function delay(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

export async function verifyHospital(code: string): Promise<boolean> {
  await delay(400);
  return code.trim().length > 0;
}

export async function verifyCredentials(email: string, password: string): Promise<boolean> {
  await delay(400);
  return email.trim().length > 0 && password.trim().length > 0;
}

export async function verifyOtp(otp: string): Promise<boolean> {
  await delay(400);
  if (otp.trim().length !== 6) return false;
  login();
  return true;
}
