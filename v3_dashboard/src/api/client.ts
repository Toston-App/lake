// Hardcoded auth token — swap for real auth later
const AUTH_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3NzQxMzE0ODMsInVzZXIiOnsibmFtZSI6InN0cmluZyIsImVtYWlsIjoidXNlcjJAZXhhbXBsZS5jb20iLCJjb3VudHJ5Ijoic3RyaW5nIiwiaWQiOjMwNX19.q_a4YzYsTnBO69GSdU2ayb867CXjEP9iTHFLUTz9QPs";

const BASE_URL = "/api/v3";

export async function apiFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const url = `${BASE_URL}${path}`;

  const res = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${AUTH_TOKEN}`,
      ...options?.headers,
    },
  });

  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API error ${res.status}: ${body}`);
  }

  return res.json() as Promise<T>;
}
