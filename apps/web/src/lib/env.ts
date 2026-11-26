/**
 * Public, environment-driven frontend configuration.
 * NEVER read or expose SUPABASE_SERVICE_ROLE_KEY or server-only secrets here.
 */
export const webEnv = {
  apiBaseUrl: (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, ""),
  supabaseUrl: (process.env.NEXT_PUBLIC_SUPABASE_URL || "").replace(/\/+$/, ""),
  supabaseAnonKey: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || "",
} as const;
